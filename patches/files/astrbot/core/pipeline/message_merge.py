# -*- coding: utf-8 -*-
"""连发合并（P0-3）：把同一会话、同一发送者的连续短消息合并成一个事件再进管道。

规则（对应 handoff P0-3）：
  1. 按会话隔离        —— 键是 (unified_msg_origin, sender_id)，不同会话互不影响
  2. 重置计时 + 上限    —— 每条新消息重置 1.6s 等待窗口，但最多只等 6s
  3. 文本拼接 + 保序    —— 文本用空格拼接；图片/语音/引用等非文本段按原顺序保留
  4. @ 时立刻冲出       —— 群里 @ 机器人（或 @全体）时把缓冲区里的消息一起带走
  5. 生成中排队        —— 上一轮还在生成时，新消息进入等待队列，生成结束后再合并处理

只对 aiocqhttp 生效（当前唯一平台），其它平台原样直通，避免误伤。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from astrbot.core import logger
from astrbot.core.message.components import At, AtAll, Plain
from astrbot.core.platform import AstrMessageEvent

MERGE_PLATFORMS = frozenset({"aiocqhttp"})
DEFAULT_WINDOW_S = 1.6
DEFAULT_MAX_WAIT_S = 6.0


def is_mergeable(event: AstrMessageEvent, platforms: frozenset[str] = MERGE_PLATFORMS) -> bool:
    try:
        return event.get_platform_name() in platforms
    except Exception:
        return False


def is_at_bot(event: AstrMessageEvent) -> bool:
    """是否 @ 了机器人本人或 @全体。"""
    try:
        self_id = str(event.get_self_id() or getattr(event.message_obj, "self_id", "") or "")
        for comp in event.message_obj.message or []:
            if isinstance(comp, AtAll):
                return True
            if isinstance(comp, At) and self_id and str(getattr(comp, "qq", "")) == self_id:
                return True
    except Exception:
        return False
    return False


def merge_events(events: list[AstrMessageEvent]) -> AstrMessageEvent:
    """把多条事件合成一条；返回作为载体的事件（尽量选带 @ 的那条）。"""
    if len(events) == 1:
        return events[0]
    base = events[0]
    for e in events:
        if is_at_bot(e):
            base = e
            break

    merged: list = []
    for e in events:
        for comp in e.message_obj.message or []:
            if isinstance(comp, Plain) and merged and isinstance(merged[-1], Plain):
                merged[-1].text = f"{merged[-1].text} {comp.text}"
            else:
                merged.append(comp)

    texts = [(e.message_str or "").strip() for e in events if (e.message_str or "").strip()]
    base.message_obj.message = merged
    base.message_str = " ".join(texts)

    last = events[-1]
    base.message_obj.message_id = last.message_obj.message_id
    base.message_obj.timestamp = last.message_obj.timestamp
    if getattr(last.message_obj, "raw_message", None):
        base.message_obj.raw_message = last.message_obj.raw_message
    return base


Runner = Callable[[AstrMessageEvent], Awaitable[None]]


class SessionMerger:
    """按会话串行化 + 连发合并。submit() 返回 True 表示已接管，False 表示调用方应立即执行。"""

    def __init__(
        self,
        runner: Runner,
        *,
        window_s: float = DEFAULT_WINDOW_S,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        platforms: frozenset[str] = MERGE_PLATFORMS,
    ) -> None:
        self._runner = runner
        self.window_s = window_s
        self.max_wait_s = max_wait_s
        self.platforms = platforms
        self._buf: dict[str, list[AstrMessageEvent]] = {}
        self._first_ts: dict[str, float] = {}
        self._timer: dict[str, asyncio.Task] = {}
        self._busy: dict[str, bool] = {}
        self._pending: dict[str, list[AstrMessageEvent]] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---------- 对外 ----------
    def submit(self, event: AstrMessageEvent) -> bool:
        if not is_mergeable(event, self.platforms):
            return False
        try:
            umo = event.unified_msg_origin
            sender = str(event.get_sender_id())
        except Exception:
            return False
        bkey = f"{umo}|{sender}"

        if self._busy.get(umo):
            self._pending.setdefault(umo, []).append(event)
            logger.info(f"[连发合并] 生成中，入队 {umo} 第 {len(self._pending[umo])} 条")
            return True

        # 同一会话里有别的发送者正在缓冲 → 先把那些冲出去，保证顺序不乱
        others = [k for k in list(self._buf) if k.startswith(f"{umo}|") and k != bkey and self._buf.get(k)]
        for k in others:
            self._flush(k)
        if self._busy.get(umo):
            self._pending.setdefault(umo, []).append(event)
            return True

        self._buf.setdefault(bkey, []).append(event)
        self._first_ts.setdefault(bkey, time.monotonic())

        if is_at_bot(event):
            logger.info(f"[连发合并] @ 触发，立即冲出 {bkey} ({len(self._buf[bkey])} 条)")
            self._flush(bkey)
            return True

        self._schedule(bkey)
        return True

    def stats(self) -> dict:
        return {
            "buffered": {k: len(v) for k, v in self._buf.items() if v},
            "busy": [k for k, v in self._busy.items() if v],
            "pending": {k: len(v) for k, v in self._pending.items() if v},
        }

    # ---------- 内部 ----------
    def _schedule(self, bkey: str) -> None:
        old = self._timer.pop(bkey, None)
        if old and not old.done():
            old.cancel()
        elapsed = time.monotonic() - self._first_ts.get(bkey, time.monotonic())
        delay = max(0.0, min(self.window_s, self.max_wait_s - elapsed))
        task = asyncio.create_task(self._sleep_then_flush(bkey, delay))
        self._timer[bkey] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sleep_then_flush(self, bkey: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._flush(bkey)

    def _flush(self, bkey: str) -> None:
        timer = self._timer.pop(bkey, None)
        if timer and not timer.done():
            timer.cancel()
        events = self._buf.pop(bkey, None)
        self._first_ts.pop(bkey, None)
        if not events:
            return
        umo = events[0].unified_msg_origin
        if self._busy.get(umo):
            self._pending.setdefault(umo, []).extend(events)
            return
        merged = merge_events(events) if len(events) > 1 else events[0]
        if len(events) > 1:
            logger.info(
                f"[连发合并] {bkey} 合并 {len(events)} 条 -> {merged.get_message_outline()[:60]!r}"
            )
        self._busy[umo] = True
        task = asyncio.create_task(self._run(umo, merged))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, umo: str, event: AstrMessageEvent) -> None:
        try:
            await self._runner(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"[连发合并] 管道执行异常 {umo}: {e}", exc_info=True)
        finally:
            self._busy.pop(umo, None)
            self._next(umo)

    def _next(self, umo: str) -> None:
        """处理排队消息：**只合并相邻且同发送者**的片段，保持原始顺序（P1-3）。"""
        queued = self._pending.pop(umo, None)
        if queued:
            def sender(ev):
                try:
                    return str(ev.get_sender_id())
                except Exception:  # noqa: BLE001
                    return "?"

            head_sender = sender(queued[0])
            batch = [queued[0]]
            idx = 1
            while idx < len(queued) and sender(queued[idx]) == head_sender:
                batch.append(queued[idx])
                idx += 1
            rest = queued[idx:]
            if rest:
                self._pending[umo] = rest
            merged = merge_events(batch) if len(batch) > 1 else batch[0]
            if len(batch) > 1:
                logger.info(f"[连发合并] 排队合并 {len(batch)} 条 -> {umo}")
            self._busy[umo] = True
            task = asyncio.create_task(self._run(umo, merged))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        if not any(k.startswith(f"{umo}|") for k in self._buf):
            self._busy.pop(umo, None)
