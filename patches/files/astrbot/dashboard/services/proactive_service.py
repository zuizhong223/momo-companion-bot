# -*- coding: utf-8 -*-
"""主动消息同源生成（P0-4）。

目的：让「Bot管家」不再自己维护一份配置副本，也不再用 Ollama/DeepSeek 裸调，
而是走 AstrBot 里**同一份**配置 + **同一份**真实会话历史 + **同一个** provider。

关键约束（验收标准）：
  - 绕开会话写入：只把生成的 assistant 消息追加进 conversations，
    绝不新增 user 轮次（一次性 instruction 不落库）。
  - 出站文本统一过质量门禁（这里只做清洗，去重交给真正的发送路径）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import time
import zoneinfo
from pathlib import Path

from astrbot.core import logger
from astrbot.core.utils.paths import project_root
from astrbot.core.agent.message import TextPart
from astrbot.core.astr_main_agent import (
    ARC_MEMORY,
    LLM_SAFETY_MODE_SYSTEM_PROMPT,
    PHASE_CUE_RE,
    WEEKDAY_NAMES,
)
from astrbot.core.utils.history_exclude import filter_history
from astrbot.core.utils.outbound_gate import apply_gate
from astrbot.core.utils.send_ledger import get_ledger
from astrbot.core.utils.style_profile import detect_mode

DEFAULT_INSTRUCTION = (
    "（这是你自己的想法冒出来的时刻，不是用户找你说话。你现在主动想给用户发一条消息。）\n"
    "要求：只写你要发出去的那句话，10~25 字；符合你平时的说话方式——短句、用空格断句、"
    "句尾最多一个半括号；不提「系统」「提示」「指令」这类词；不要问用户问题，不要复述用户刚才说过的话；"
    "可以分享你此刻在做什么（练习/打崩铁/上课/吃饭/困了/想用户），或者单纯冒出的一句话。\n"
    "只输出这一条消息本身，不要引号，不要解释。"
)

DEFAULT_MAX_CHARS = 40
DEFAULT_MAX_HISTORY = 80

# 主动消息检索：只承接“机器人刚说过的话”；没有 assistant 时，只接“像问题”的用户消息。
# 不能再用“用户上一句”当 query——14:07 的 live 例子就是用户说“我天天都很关心你”，
# 结果检索出一堆旧事，模型顺手编了个不存在的视频。
_QUESTION_HINTS = ("吗", "什么", "怎么", "哪", "几点", "谁", "为什么", "？", "?", "记得",
                   "干嘛", "在吗", "多少", "多久")

_TOPIC_STOP = {
    "就是", "然后", "反正", "那个", "一个", "觉得", "真的", "有点", "还是", "可以",
    "什么", "自己", "现在", "今天", "已经", "所以", "但是", "因为", "这个", "我们",
    "用户们", "起来", "时候", "不想", "知道", "看到", "想起", "离谱",
}


def _topic_grams(text: str) -> set[str]:
    t = "".join(ch for ch in (text or "") if "\u4e00" <= ch <= "\u9fff")
    grams = {t[i:i + 2] for i in range(len(t) - 1)}
    return grams - _TOPIC_STOP


def topic_duplicate(old_texts: list[str], text: str,
                    threshold: float = 0.32) -> str | None:
    """跨天重复话题：2-gram 包含度（不是整句相似度）。

    示例：同一话题换个说法，整句相似度不高，但主题词可能重合 4~7 个；
    用 min(|A|,|B|) 做分母，比 Jaccard 更适合“新稿更长、旧梗是子集”的情况。
    """
    a = _topic_grams(text)
    if len(a) < 4:
        return None
    for old in old_texts:
        b = _topic_grams(old)
        if len(b) < 4:
            continue
        hit = len(a & b)
        if hit >= 4 and hit / min(len(a), len(b)) >= threshold:
            return old
    return None


def _message_text(m: dict) -> str:
    content = m.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") in (None, "text")
        )
    return ""


def _clean_query(text: str) -> str:
    text = (text or "").split("<system_reminder>")[0]
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_kb_query(history: list | None, last_query: str = "") -> str:
    """选一个“可承接”的检索 query；选不到/和上轮重复就不检索。

    优先级：
    1) 机器人最近一条有内容的 assistant（承接自己的话题）；
    2) 最近一条“像问题”的用户消息；
    3) 都没有 → 空字符串。
    """
    q = ""
    for m in reversed(history or []):
        if m.get("role") == "assistant":
            t = _clean_query(_message_text(m))
            if len(t) >= 4:
                q = t[:60]
                break
    if not q:
        for m in reversed(history or []):
            if m.get("role") == "user":
                t = _clean_query(_message_text(m))
                if t and any(k in t for k in _QUESTION_HINTS):
                    q = t[:60]
                    break
    if q and last_query:
        if q == last_query:
            return ""
        # 同一话题换个说法也要挡住：2-gram 重合度过高直接跳过。
        a = _topic_grams(q)
        b = _topic_grams(last_query)
        if a and b:
            hit = len(a & b)
            if hit >= 3 and hit / min(len(a), len(b)) >= 0.5:
                return ""
    return q


# 进程级、按 umo 记住上一轮 query；ProactiveService 是每请求新建的，实例属性会丢。
_LAST_KB_QUERY: dict[str, str] = {}


def _time_phrase(hour: int) -> str:
    if 5 <= hour < 9:
        return "早上"
    if 9 <= hour < 12:
        return "上午"
    if 12 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _with_time_context(instruction: str, now: datetime.datetime) -> str:
    """把“现在几点/什么时段”放到 instruction 最前面，压住模型说错早上/晚上。"""
    return (f"（现在是 {now.strftime('%H:%M')}，{_time_phrase(now.hour)}。"
            "不要把这个时段说错，也不要说成别的时段。）\n" + instruction)


def _recent_sources(limit: int = 2, root: str | Path | None = None) -> list[dict]:
    root_p = Path(root or project_root())
    p = root_p / "ops" / "external_seen.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("stale") or not rec.get("title") or not rec.get("url"):
                continue
            out.append(rec)
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        return []
    return out


def _sources_context(limit: int = 2, root: str | Path | None = None) -> str:
    rows = _recent_sources(limit, root)
    if not rows:
        return ""
    body = "；".join(f"{r['title']}（{r['url']}）" for r in rows)
    return ("<system_reminder>你最近刷到过：" + body +
            "。只在合适时提一句，不要编造没读过的内容，也不要假装刚看到。"
            "</system_reminder>")


class ProactiveServiceError(Exception):
    pass


class ProactiveService:
    def __init__(self, db, core_lifecycle) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.ctx = core_lifecycle.star_context

    # ---------- 工具 ----------
    @staticmethod
    def _strip_media(contexts: list) -> list:
        """历史里的 image_url 段（base64）对纯文本模型毫无意义，去掉，省 token。"""
        out = []
        for msg in contexts:
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, list):
                    kept = [
                        p for p in content
                        if not (isinstance(p, dict) and p.get("type") in ("image_url", "audio_url"))
                    ]
                    if not kept:
                        continue
                    out.append({**msg, "content": kept})
                else:
                    out.append(msg)
            else:
                out.append(msg)
        return out

    async def _resolve(self, umo: str) -> dict:
        ctx = self.ctx
        try:
            cfg = ctx.get_config(umo=umo)
        except Exception as exc:  # noqa: BLE001
            raise ProactiveServiceError(f"读取配置失败: {exc}") from exc

        conv_mgr = ctx.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if not cid:
            raise ProactiveServiceError(f"会话不存在，无法沿用真实历史: {umo}")
        conv = await conv_mgr.get_conversation(umo, cid)
        if conv is None:
            raise ProactiveServiceError(f"会话读取失败: {umo}")

        runner_cfg = cfg.get("agent_runner", {}).get("config", {})
        provider_id = runner_cfg.get("model", {}).get("provider_id", "")
        provider = ctx.get_provider_by_id(provider_id) if provider_id else None
        if provider is None:
            try:
                provider = await ctx.get_using_provider_async(umo=umo)
            except Exception:  # noqa: BLE001
                provider = None
        if provider is None:
            raise ProactiveServiceError(f"没有可用的对话模型: provider_id={provider_id!r}")

        persona_cfg = runner_cfg.get("persona", {})
        provider_settings = dict(cfg.get("provider_settings", {}) or {})
        provider_settings.setdefault(
            "default_personality", persona_cfg.get("persona_id", "default"))
        persona_id, persona, _, _ = await ctx.persona_manager.resolve_selected_persona(
            umo=umo,
            conversation_persona_id=conv.persona_id,
            platform_name=umo.split(":", 1)[0] if ":" in umo else "aiocqhttp",
            provider_settings=provider_settings,
        )
        if not persona:
            raise ProactiveServiceError(f"找不到配置: {persona_id!r}")
        return {
            "cfg": cfg,
            "conv": conv,
            "provider": provider,
            "persona_id": persona_id,
            "persona": persona,
            "persona_cfg": persona_cfg,
            "timezone": cfg.get("timezone"),
        }

    def _build_prompt(self, info: dict) -> tuple[str, list, list, list]:
        """返回 (system_prompt, dialogs, history, extra_parts)。

        风格示例与历史分开返回，由调用方按预算各自裁剪（P1-2：别让历史把示例挤掉）。
        """
        persona = info["persona"]
        raw_hist = json.loads(info["conv"].history or "[]")
        persona_prompt = persona.get("prompt") or ""
        _early_mode = False
        _arc_memory = ""
        # D=A：主动也始终用早期语气；吵架/和好只作为记忆，不切配置。
        try:
            _phase = os.environ.get("DEMO_PHASE", "auto").strip().lower()
            if _phase != "continuity":
                _p = Path(os.environ.get("DEMO_ROOT", r"D:/demo")) / "ops/persona/persona_early.md"
                if _p.is_file():
                    persona_prompt = _p.read_text(encoding="utf-8")
                    _early_mode = True
                    if _phase != "early":
                        _arc_memory = ARC_MEMORY
        except Exception:  # noqa: BLE001
            pass
        system_prompt = f"# Persona Instructions\n\n{persona_prompt}\n"
        if info["persona_cfg"].get("safety_mode", True):
            system_prompt = f"{LLM_SAFETY_MODE_SYSTEM_PROMPT}\n\n{system_prompt}"
        if _arc_memory:
            system_prompt += _arc_memory

        history = filter_history([dict(m) for m in raw_hist],
                                 info["conv"].user_id)
        if _early_mode:
            history = [
                m for m in history
                if not PHASE_CUE_RE.search(str(m.get("content") or ""))
            ]
        dialogs = [dict(d) for d in (persona.get("_begin_dialogs_processed") or [])]

        extra_parts = []
        tzname = info["timezone"]
        try:
            now = (datetime.datetime.now(zoneinfo.ZoneInfo(tzname)) if tzname
                   else datetime.datetime.now().astimezone())
        except Exception:  # noqa: BLE001
            now = datetime.datetime.now().astimezone()
        extra_parts.append(TextPart(text=(
            "<system_reminder>Current datetime: "
            f"{now.strftime('%Y-%m-%d %H:%M (%Z)')}, "
            f"Weekday: {WEEKDAY_NAMES[now.weekday()]}</system_reminder>"
        )))
        return system_prompt, dialogs, history, extra_parts

    async def _save_assistant(self, info: dict, umo: str, text: str, usage) -> None:
        """只追加 assistant，不写 user。"""
        conv_mgr = self.ctx.conversation_manager
        conv = await conv_mgr.get_conversation(umo, info["conv"].cid)
        if conv is None:
            raise ProactiveServiceError("保存失败：会话已不存在")
        history = json.loads(conv.history or "[]")
        before_roles = [m.get("role") for m in history]
        history.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
        token_usage = getattr(usage, "total", None) if usage is not None else None
        await conv_mgr.update_conversation(
            umo, info["conv"].cid, history=history, token_usage=token_usage)
        after = json.loads(
            (await conv_mgr.get_conversation(umo, info["conv"].cid)).history or "[]")
        if [m.get("role") for m in after].count("user") != before_roles.count("user"):
            logger.error("[主动同源] 检测到 user 轮次变化，请检查保存逻辑")


    async def _kb_hint(self, umo: str, history: list) -> str:
        """按“可承接的话题”查一次知识库；选不到 query 就不检索。"""
        query = _pick_kb_query(history, _LAST_KB_QUERY.get(umo, ""))
        if not query:
            return ""
        _LAST_KB_QUERY[umo] = query
        try:
            from astrbot.core.tools.knowledge_base_tools import (
                retrieve_knowledge_base,
            )

            text = await retrieve_knowledge_base(query, umo, self.ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[主动同源] 知识库检索失败（忽略）: {e}")
            return ""
        if not text:
            return ""
        text = text.strip()
        # A09：按“完整证据块”限预算，不机械砍前 300 字。
        # 主动消息只需要 1~2 块，600 字足够；超了退到最后一个块边界。
        if len(text) > 600:
            cut = text[:600]
            for sep in ("\n\n", "\n", "。", "；", " "):
                idx = cut.rfind(sep)
                if idx >= 300:
                    cut = cut[: idx + len(sep)]
                    break
            text = cut.rstrip() + "…"
        logger.info(f"[主动同源] 知识库命中 {len(text)} 字（query={query[:20]!r}）")
        return text

    # ---------- 主入口 ----------
    async def generate(
        self,
        umo: str,
        instruction: str = "",
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_history: int = DEFAULT_MAX_HISTORY,
        save: bool = True,
        retry_dup: bool = False,
    ) -> dict:
        t0 = time.monotonic()
        # 主动静默时段：默认 9-23；DEMO_PROACTIVE_FORCE=1 可绕过（手动/测试）
        if os.environ.get("DEMO_PROACTIVE_FORCE", "0").strip().lower() not in (
                "1", "true", "on", "yes"):
            _hour = datetime.datetime.now().hour
            if not (9 <= _hour < 23):
                raise ProactiveServiceError(
                    f"静默时段 9-23（当前 {_hour} 点），跳过主动")
        info = await self._resolve(umo)
        system_prompt, dialogs, history, extra_parts = self._build_prompt(info)
        kb_hint = await self._kb_hint(umo, history)
        # P3-19 / P3-20：结构化事实 + 情绪状态（各自很短的提示，都没命中就不加）
        try:
            from astrbot.core.utils.memory_facts import format_for_prompt as _facts
            from astrbot.core.utils.mood_state import format_for_prompt as _mood
            for hint in (_facts(info["conv"].user_id), _mood(info["conv"].user_id)):
                if hint:
                    from astrbot.core.agent.message import TextPart
                    extra_parts.append(TextPart(text=hint))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[主动同源] 事实/情绪提示跳过: {e}")
        # F1：待绑定的事件 id（真正 told 要等发送成功）
        _pending_event = None
        # 生活层影子/灰度：默认 DEMO_LIFE=0，不改变现有输出
        try:
            from astrbot.core.agent.message import TextPart
            from astrbot.core.utils.life_state import commitments_context as _commit_ctx
            from astrbot.core.utils.life_state import enabled as _life_on
            from astrbot.core.utils.life_state import format_conflicts as _format_conflicts
            from astrbot.core.utils.life_state import life_context as _life_ctx
            from astrbot.core.utils.life_state import relation_db as _relation_db
            from astrbot.core.utils.life_state import shadow as _life_shadow

            # P1-B09：冲突/承诺读关系正式库，不跟生活层 shadow 切库，也不依赖生活开关。
            _rel_db = _relation_db()
            _cc = _commit_ctx(umo, db_path=_rel_db)
            _cf = _format_conflicts(umo, db_path=_rel_db)
            if _cf:
                extra_parts.append(TextPart(text=_cf))
                logger.info(f"[主动关系] 冲突上下文: {_cf[:60]}")
            if _cc:
                extra_parts.append(TextPart(text=_cc))
                logger.info(f"[主动关系] 承诺上下文: {_cc[:60]}")

            if _life_on():
                _lc = _life_ctx(umo, "proactive")
                _sc = _sources_context()
                if _life_shadow():
                    logger.info(f"[生活层影子] proactive v{_lc['version']}: {_lc['text'][:80]}")
                    if _sc:
                        logger.info(f"[生活层影子] 来源: {_sc[:100]}")
                else:
                    if _lc.get("text"):
                        extra_parts.append(TextPart(text=_lc["text"]))
                        # F1：这里**不能**标 told（还没生成、没发送）。
                        # 记下事件 id，等真正发出去且文本确实提到它再提交。
                        _pending_event = _lc.get("source_event_id")
                        logger.info(f"[生活层] 主动上下文 v{_lc['version']}: {_lc['text'][:60]}")
                    if _sc:
                        extra_parts.append(TextPart(text=_sc))
                        logger.info(f"[生活层] 来源上下文: {_sc[:60]}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[生活层] 主动上下文跳过: {e}")
        if kb_hint:
            from astrbot.core.agent.message import TextPart

            extra_parts.append(TextPart(text=(
                "（下面是可以顺带提一句的旧事，只当参考，不要照抄、不要明说是资料：\n"
                + kb_hint + "）")))
        # 示例优先占预算：先留风格示例，再给历史剩下的名额
        hist_budget = max(0, max_history - len(dialogs)) if max_history else len(history)
        history = history[-hist_budget:] if hist_budget else []
        contexts = self._strip_media(dialogs + history)
        instruction = instruction or DEFAULT_INSTRUCTION
        try:
            tzname = info.get("timezone")
            _now = (datetime.datetime.now(zoneinfo.ZoneInfo(tzname)) if tzname
                    else datetime.datetime.now().astimezone())
            instruction = _with_time_context(instruction, _now)
            logger.info(f"[主动时段] {_now.strftime('%H:%M')} {_time_phrase(_now.hour)}")
        except Exception:  # noqa: BLE001
            pass

        provider = info["provider"]
        attempts = 0
        raw = ""
        usage = None
        while attempts < 2:
            attempts += 1
            try:
                resp = await provider.text_chat(
                    prompt=instruction,
                    contexts=contexts,
                    system_prompt=system_prompt,
                    session_id=umo,
                    extra_user_content_parts=extra_parts,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[主动同源] provider 调用失败: {exc}", exc_info=True)
                raise ProactiveServiceError(f"provider 调用失败: {exc}") from exc
            raw = (resp.completion_text or "").strip()
            usage = getattr(resp, "usage", None)
            if raw and len(raw) <= max_chars:
                break
            if attempts == 1:
                logger.info(f"[主动同源] 首次输出过长/为空({len(raw)}字)，重试一次")
                instruction = instruction + "\n再短一点，不要超过 25 个字。"
                # A09：只改“本次表达长度”，保留同一份上下文（时间/事实/情绪/KB）
        text, actions = apply_gate(
            raw, session_key=f"p:{umo}", dedupe=False, record=False,
            mode=detect_mode(raw))
        if not text:
            raise ProactiveServiceError("生成结果为空或被门禁丢弃")
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
            actions.append(f"截断到 {max_chars} 字")

        # 历史不在这里写（P0-B）：由实际发送路径带 managed_history 提交。
        # L2 主动话题相似度抑制：整句 + 7 天主题词，太像就换话题重生成一次。
        try:
            ledger = get_ledger()
            dup = ledger.recent_similar(f"p:{umo}", text,
                                        window_s=7 * 24 * 3600, ratio=0.80)
            if not dup:
                dup = topic_duplicate(
                    ledger.recent_texts(f"p:{umo}", 7 * 24 * 3600), text)
        except Exception:  # noqa: BLE001
            dup = None
        if dup and not retry_dup:
            logger.info(f"[主动同源] 与最近发送过像（{dup[:12]}…），换个话题重生成")
            base = instruction or DEFAULT_INSTRUCTION
            return await self.generate(
                umo, base + "\n（换一个话题，不要重复最近说过的旧梗）",
                max_chars=max_chars, max_history=max_history,
                save=save, retry_dup=True)

        # F1：生成完再登记"可能要说这件事"，等发送成功后由 open_api 提交
        if _pending_event:
            try:
                from astrbot.core.utils.life_state import note_told_pending as _note

                _note(umo, _pending_event, text)
            except Exception:  # noqa: BLE001
                pass

        return {
            "text": text,
            "raw": raw,
            "actions": actions,
            "persona_id": info["persona_id"],
            "provider": (provider.provider_config or {}).get("id", ""),
            "model": provider.get_model(),
            "contexts": len(contexts),
            "attempts": attempts,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "saved": False,
            "delivery": "caller",   # 由调用方发送，发送成功时提交历史
        }

    async def _commit_removed(self) -> None:
        """（已删除）原先的 commit 端点：它完全信任调用方传来的 sent=True。

        现在历史提交由真实发送结果驱动（见 AiocqhttpMessageEvent._tracked_send），
        这个入口不再存在，避免伪造。
        """
        return None
        return {"state": ledger.get(task_id)["state"], "history": history}
