# -*- coding: utf-8 -*-
"""隔离测试用的假 Bot：覆盖全部发送出口，绝不碰网络。

用法：
    bot = FakeBot(mode="ok")
    await AiocqhttpMessageEvent.send_message(bot=bot, ...)
    bot.calls          # 记录到的调用
    bot.record_path    # 可选：写到 jsonl

mode:
    ok              正常返回 {"message_id": N}
    timeout         抛 asyncio.TimeoutError
    definite        抛 ActionFailed(retcode=100)（明确拒绝）
    accepted_lost   先"接收"再抛 ConnectionResetError（平台已接受但连接断了）
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path


def _make_action_failed(retcode: int, message: str):
    """尽量用 aiocqhttp 真实异常，保证 classify_exception 走的是生产同一条分支。"""
    try:
        from aiocqhttp.exceptions import ActionFailed

        return ActionFailed({"retcode": retcode, "message": message, "data": None})
    except Exception:  # noqa: BLE001
        return FakeActionFailed(retcode, message)


class FakeBot:
    _ALLOWED = {
        "send_group_msg",
        "send_private_msg",
        "send",
        "call_action",
        "on_request",
        "on_notice",
        "on_message",
        "on_websocket_connection",
        "run_task",
    }

    def __init__(self, mode: str = "ok", record_path: str | Path | None = None,
                 latency: float = 0.0) -> None:
        self.mode = mode
        self.latency = latency
        self.calls: list[dict] = []
        self._seq = 0
        self.record_path = Path(record_path) if record_path else None
        if self.record_path:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- 内部 ----
    def _log(self, name: str, **kwargs) -> None:
        self._seq += 1
        item = {"n": self._seq, "call": name, "t": round(time.time(), 3),
                "kwargs": {k: (v if isinstance(v, (int, str, float, list, dict, type(None))) else str(v))
                           for k, v in kwargs.items()}}
        self.calls.append(item)
        if self.record_path:
            with self.record_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def _behave(self, name: str, **kwargs) -> dict:
        if self.latency:
            await asyncio.sleep(self.latency)
        self._log(name, **kwargs)
        if self.mode == "ok":
            return {"message_id": self._seq * 1000}
        if self.mode == "timeout":
            raise asyncio.TimeoutError(f"fake timeout in {name}")
        if self.mode == "definite":
            raise _make_action_failed(100, "fake rejected")
        if self.mode == "accepted_lost":
            # 平台已经接收（已记录），但返回路上连接断了
            raise ConnectionResetError("fake connection reset after accept")
        raise RuntimeError(f"unknown fake mode: {self.mode}")

    # ---- 发送接口 ----
    async def send_group_msg(self, **kwargs):
        return await self._behave("send_group_msg", **kwargs)

    async def send_private_msg(self, **kwargs):
        return await self._behave("send_private_msg", **kwargs)

    async def send(self, **kwargs):
        return await self._behave("send", **kwargs)

    async def call_action(self, action: str, **kwargs):
        return await self._behave("call_action", action=action, **kwargs)

    # ---- 适配器注册用（不启真实连接）----
    def on_request(self):
        return lambda fn: fn

    def on_notice(self):
        return lambda fn: fn

    def on_message(self, _type=None):
        return lambda fn: fn

    def on_websocket_connection(self):
        return lambda fn: fn

    async def run_task(self, **_kwargs):
        return None

    def __getattr__(self, name):  # 任何未覆盖的方法一律炸掉，防止偷偷走真实网络
        raise AttributeError(
            f"FakeBot 不支持 {name}（隔离环境里不允许调用真实网络接口）")
