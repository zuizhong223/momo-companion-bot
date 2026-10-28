import asyncio
import base64
import json as _json
import os
import random
import re
import time
import urllib.request
import uuid
from collections import deque
from collections.abc import AsyncGenerator

from aiocqhttp import CQHttp, Event

from astrbot.api import logger
from astrbot.core.utils.paths import project_dir
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    At,
    BaseMessageComponent,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Video,
)
from astrbot.api.platform import Group, MessageMember
from astrbot.core.utils.outbound_gate import apply_gate as _apply_gate
from astrbot.core.utils.outbound_gate import commit_style as _commit_style
from astrbot.core.utils.style_profile import detect_mode as _detect_mode
from astrbot.core.utils.style_profile import emotion_tags as _emotion_tags
from astrbot.core.utils.style_profile import should_sticker as _should_sticker
from astrbot.core.utils.style_profile import sticker_pool as _sticker_pool
from astrbot.core.utils.style_profile import vary_haohao as _vary_haohao
from astrbot.core.utils.style_profile import shorten_banter as _shorten_banter
from astrbot.core.utils.style_profile import laugh_nudge as _laugh_nudge
from astrbot.core.utils.style_profile import reinforce_decision as _reinforce_decision

# 本地 3B 改写默认关闭（P1-2）：实测它会改错意思，先关掉做对照评测
_REWRITE_ENABLED = os.environ.get("DEMO_REWRITE", "0").strip().lower() not in ("0", "", "false", "off", "no")
_REWRITE_SKIP = re.compile(r"[0-9]|http|www|://|QQ|B站|bilibili")

STICKER_COLLECT_PATH = str(project_dir("表情收藏.jsonl"))
# 示例表情规则；公开版不包含真实表情库、频率或来源。
# 频率、来源、上下文都在 bank.jsonl；不再用小黄脸/emoji。
STICKER_BANK_PATH = str(project_dir("表情库", "bank.jsonl"))
_STICKER_CACHE: dict = {"mtime": None, "items": []}
_RECENT_STICKERS: deque = deque(maxlen=3)
_STICKER_QUOTA: dict = {"day": None, "n": 0}


def _sticker_budget(max_per_day: int | None = None) -> int:
    if max_per_day is None:
        try:
            max_per_day = int(os.environ.get("DEMO_MAX_STICKERS_PER_DAY", "80") or 80)
        except (TypeError, ValueError):
            max_per_day = 80
    return int(max_per_day)


def _sticker_quota_reset_today() -> None:
    today = time.strftime("%Y-%m-%d")
    if _STICKER_QUOTA.get("day") != today:
        _STICKER_QUOTA["day"] = today
        _STICKER_QUOTA["n"] = 0


def _sticker_quota_state(max_per_day: int | None = None) -> tuple[bool, float]:
    """返回 (还能不能尝试发, 概率倍率)。不消耗预算。

    日预算用完后降到 35% 概率继续（"适量"），到 2 倍预算才彻底停。
    """
    budget = _sticker_budget(max_per_day)
    if budget <= 0:
        return True, 1.0
    _sticker_quota_reset_today()
    n = int(_STICKER_QUOTA.get("n") or 0)
    hard_mul = float(os.environ.get("DEMO_STICKER_HARD_MULT", "2") or 2)
    taper = float(os.environ.get("DEMO_STICKER_TAPER", "0.35") or 0.35)
    hard = int(budget * max(1.0, hard_mul))
    if n >= hard:
        return False, 0.0
    if n < budget:
        return True, 1.0
    return True, max(0.0, min(1.0, taper))


def _sticker_quota_consume() -> None:
    """真正发出后才占预算。"""
    _sticker_quota_reset_today()
    _STICKER_QUOTA["n"] = int(_STICKER_QUOTA.get("n") or 0) + 1


_PACING_LOG = {"path": None, "checked": False}


def _log_pacing(delay: float, total_len: int, bot=None) -> None:
    """把回复延迟写进 ops/alerts/pacing.jsonl（512KB 轮转）。

    日志会随重启轮转，历史延迟就查不到了；这里单独留一份，
    才能回答"机器人回得是不是太慢"（p50/p90 见 ops/eval/pacing_dist.py）。

    2026-10-28：**假 bot / 测试环境不记**。
    另一个会话跑模拟时会走发送链路，延迟会被记进来，把真实统计冲淡
    （实测 35 条样本里没有一条对应台账记录）。
    """
    try:
        if bot is not None and any(
                k in type(bot).__name__.lower() for k in ("fake", "mock", "sim", "dummy")):
            return
        if os.environ.get("DEMO_TEST") == "1":
            return
        if not _PACING_LOG["checked"]:
            from astrbot.core.utils.paths import ops_dir

            _PACING_LOG["path"] = ops_dir("alerts", "pacing.jsonl")
            _PACING_LOG["checked"] = True
        path = _PACING_LOG["path"]
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 512 * 1024:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(
                {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "delay": round(float(delay), 1), "len": int(total_len)},
                ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass   # 计时统计失败绝不影响发送


def _sticker_quota_allowed(max_per_day: int | None = None) -> bool:
    """Q9：贴纸每天硬上限（保持原语义，测试依赖它）；0/负数表示不限。"""
    max_per_day = _sticker_budget(max_per_day)
    if max_per_day <= 0:
        return True
    today = time.strftime("%Y-%m-%d")
    if _STICKER_QUOTA.get("day") != today:
        _STICKER_QUOTA["day"] = today
        _STICKER_QUOTA["n"] = 0
    if int(_STICKER_QUOTA["n"]) >= max_per_day:
        return False
    _STICKER_QUOTA["n"] = int(_STICKER_QUOTA["n"]) + 1
    return True


def _load_sticker_bank() -> list[dict]:
    """读取表情库（按 mtime 缓存）。只保留文件存在、≤400KB 的条目。"""
    try:
        mtime = os.path.getmtime(STICKER_BANK_PATH)
    except OSError:
        return []
    if _STICKER_CACHE["mtime"] == mtime:
        return _STICKER_CACHE["items"]
    items: list[dict] = []
    base = os.path.dirname(STICKER_BANK_PATH)
    try:
        with open(STICKER_BANK_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = _json.loads(line)
                path = os.path.join(base, "files", str(obj.get("file") or ""))
                if not obj.get("file") or not os.path.exists(path):
                    continue
                if int(obj.get("bytes") or 0) > 400 * 1024:
                    continue
                obj["path"] = path
                items.append(obj)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[贴纸] 表情库读取失败: {e}")
        return []
    _STICKER_CACHE.update(mtime=mtime, items=items)
    return items


def _pick_sticker(mode: str = "banter", user_text: str = "",
                  reply_text: str = "", umo: str | None = None) -> dict | None:
    """按“情绪标签 + 机器人用过频次”加权抽一张；最近 3 张不重复。"""
    items = _load_sticker_bank()
    if not items:
        return None
    valence = energy = None
    try:
        from astrbot.core.utils.mood_state import current as _mood_current
        valence, energy = _mood_current(umo, "her")
    except Exception:  # noqa: BLE001
        pass
    tags = _emotion_tags(user_text, reply_text, mode, valence, energy)
    pool = _sticker_pool(items, mode, tuple(_RECENT_STICKERS), tags)
    weights = [max(1, int(x.get("count") or 1)) for x in pool]
    try:
        chosen = random.choices(pool, weights=weights, k=1)[0]
    except Exception:  # noqa: BLE001
        chosen = random.choice(pool)
    _RECENT_STICKERS.append(chosen.get("md5"))
    logger.info("[贴纸] mode=%s emotion=%s -> %s", mode, sorted(tags),
                chosen.get("file"))
    return chosen


def _sticker_segment(entry: dict) -> dict | None:
    """把本地表情转成 OneBot image 段（sub_type=1 = 动画表情，不是图片气泡）。"""
    path = entry.get("path")
    if not path:
        return None
    try:
        data = open(path, "rb").read()
        if not data or len(data) > 400 * 1024:
            return None
        b64 = base64.b64encode(data).decode("ascii")
    except Exception:  # noqa: BLE001
        return None
    return {
        "type": "image",
        "data": {
            "file": f"base64://{b64}",
            "sub_type": 1,
            "summary": "[动画表情]",
        },
    }


def _collect_sticker(seg_type: str, data: dict):
    """收藏一个贴纸段（mface 或 sticker 图片）。保留给入站适配器调用。"""
    try:
        entry = {
            "type": seg_type,
            "id": data.get("id"),
            "url": data.get("url", ""),
            "emoji_package_id": data.get("emoji_package_id"),
            "key": data.get("key"),
            "t": int(__import__("time").time()),
        }
        with open(STICKER_COLLECT_PATH, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _ledger_call(fn, *args, **kwargs):
    """把同步 SQLite 台账挪到线程池，避免阻塞 asyncio 事件循环。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _worst_state(results: list[dict]) -> dict:
    order = {"unknown": 0, "failed": 1, "skipped": 2, "sent": 3}
    if not results:
        return {"state": "sent"}
    worst = min(results, key=lambda r: order.get(r.get("state", "unknown"), 0))
    return worst


# 被动回复的短句去重：只挡“同一会话 45 分钟内几乎一模一样”的话，
# 不碰主动/接口发送（那两条链路由台账的 token 语义负责）。
_PASSIVE_SEEN: dict[str, deque] = {}


def _passive_duplicate(text: str, umo: str) -> bool:
    """只检查，不登记。

    P0：旧实现先登记再发送，首发失败后同文会被当成“已经说过”而永久跳过。
    现在由 send() 在确认 sent 后调用 _passive_mark_sent() 登记。
    """
    if len(text) < 4:
        return False
    now = time.monotonic()
    hist = _PASSIVE_SEEN.setdefault(umo, deque(maxlen=12))
    for old, ts in hist:
        if now - ts > 45 * 60:
            continue
        if old == text:
            return True
        if len(old) >= 6 and len(text) >= 6:
            import difflib

            if difflib.SequenceMatcher(None, old, text).ratio() >= 0.92:
                return True
    return False


def _passive_mark_sent(text: str, umo: str) -> None:
    """确认 sent 后才登记；失败/未知/跳过都不进“已送达”去重表。"""
    if len(text) < 4:
        return
    _PASSIVE_SEEN.setdefault(umo, deque(maxlen=12)).append(
        (text, time.monotonic())
    )


def _duplicate_fallback(mode: str) -> str:
    """重复时给中性回应，不再生成“刚说过了/你说的”这类确认句。

    确认句预算会把第二次“刚说过了”整条删掉，导致静默；这里改用不触发
    确认预算的短回应，保证用户仍收到一句。
    """
    if mode == "serious":
        return "我在听（"
    if mode == "comfort":
        return "我在呢（"
    return "嗯嗯（"


# ---------- 跨轮语义去重 + 相关性守门（轻量，不调外部模型） ----------
_SEM_CTX: dict[str, deque] = {}
_META_WORDS = (
    "任务", "系统", "指令", "代码", "模型", "数据库", "服务器", "报错", "接口",
    "文件", "项目", "配置", "日志", "部署", "脚本", "程序", "算法",
)
_ALLOW_PREFIX = (
    "好", "嗯", "啊", "诶", "在", "怎么", "摸摸", "晚安", "早", "安（", "去吧",
    "行", "可以", "不要", "不知道", "这我哪", "想）", "喜欢）", "那还是",
    "多睡", "早点睡", "别熬", "都快", "这么晚", "笑死", "哈哈", "哦", "对",
    "不", "没", "我", "你", "用户",
)


def _sem_ctx(key: str) -> deque:
    return _SEM_CTX.setdefault(key, deque(maxlen=24))


def _sem_record_user(key: str, text: str) -> None:
    if not text:
        return
    d = _sem_ctx(key)
    if d and d[-1][0] == "user" and d[-1][1] == text:
        return
    d.append(("user", text, ""))


def _sem_record_bot(key: str, text: str, user_text: str = "") -> None:
    if not text:
        return
    if not user_text:
        # 没显式给就配最近一条 user，宁可少判重复也不误杀。
        for item in reversed(_sem_ctx(key)):
            if item[0] == "user":
                user_text = item[1]
                break
    _sem_ctx(key).append(("bot", text, user_text))


def _grams2(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _sim2(a: str, b: str) -> float:
    ga, gb = _grams2(a), _grams2(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / ((len(ga) * len(gb)) ** 0.5)


def _overlap2(a: str, b: str) -> int:
    return len(_grams2(a) & _grams2(b))


_CONTENT_STOP = set("你我用户机器人它的了在是就都也还这那有没不吗啊吧呢好嗯哦呀嘛哈")


def _content_chars(text: str) -> set[str]:
    return {c for c in (text or "") if "\u4e00" <= c <= "\u9fff" and c not in _CONTENT_STOP}


def _allowed_reply(text: str) -> bool:
    # 只有短句才按“我/你/在…”前缀放行；长句不能靠前缀逃过相关性检查。
    if len(text) <= 6 and text.startswith(_ALLOW_PREFIX):
        return True
    return any(k in text for k in ("点了", "睡", "晚安", "路上小心", "笑死", "哈哈"))


def _sem_fallback(mode: str, recent: list[str]) -> str:
    pool = {
        "serious": ["我在听（", "你说（", "嗯 你说"],
        "comfort": ["我在呢（", "摸摸", "怎么了（"],
        "support": ["好好好", "怎么啦（", "嗯嗯"],
    }.get(mode, ["嗯嗯", "啊？", "在呢", "怎么了（", "好耶"])
    for _ in range(6):
        x = random.choice(pool)
        if all(_sim2(x, r) < 0.6 for r in recent):
            return x
    return random.choice(pool)


def _semantic_guard(text: str, user_text: str, session_key: str,
                    mode: str) -> tuple[str, list[str]]:
    """跨轮重复 -> 换一句；回复里出现上下文没有的 meta/技术词 -> 换成安全短句。"""
    if not text:
        return text, []
    ctx = list(_sem_ctx(session_key))
    pairs = [(t, u) for r, t, u in ctx if r == "bot"][-8:]
    bots = [t for t, _ in pairs]
    # Q4：短口头禅（好好好/嗯嗯）不参与相似度，但同语境连续复读要换一句。
    if len(text) <= 4 and user_text and pairs:
        old, old_user = pairs[-1]
        if text == old and old_user and _sim2(user_text, old_user) >= 0.55:
            fb = _sem_fallback(mode, bots[-4:])
            _sem_record_bot(session_key, fb, user_text)
            return fb, [f"短复读({text})→{fb}"]
    if len(text) >= 4 and user_text:
        for old, old_user in pairs:
            if _sim2(text, old) < 0.72:
                continue
            # 只有“这一轮用户说的话”和旧回复对应的用户句也在同一话题，
            # 才算真的跨轮重复；否则短句/口头禅很容易被误杀。
            if old_user and _sim2(user_text, old_user) >= 0.55:
                fb = _sem_fallback(mode, bots[-4:])
                _sem_record_bot(session_key, fb, user_text)
                return fb, [f"跨轮语义重复({old[:8]}…)→{fb}"]
    if user_text:
        ctx_text = " ".join(t for _, t, _ in ctx[-12:])
        meta = [w for w in _META_WORDS if w in text]
        if meta and not any(w in ctx_text for w in meta):
            fb = _sem_fallback(mode, bots[-4:])
            _sem_record_bot(session_key, fb, user_text)
            return fb, [f"相关性守门(元话题 {meta[0]})→{fb}"]
        if len(text) >= 8 and not _allowed_reply(text) and _overlap2(text, ctx_text) == 0:
            # 2-gram 零重叠往往只是换了说法；再看“内容汉字”是否一个都不共享。
            # 06:39「那很坏了 菜还看到大半夜」和「这个战队好菜」共享“菜/看”，要放行。
            shared = _content_chars(text) & _content_chars(ctx_text)
            if not shared:
                fb = _sem_fallback(mode, bots[-4:])
                _sem_record_bot(session_key, fb, user_text)
                return fb, [f"相关性守门(零内容字)→{fb}"]
            logger.info(f"[相关性预警] 2-gram 零重叠但共享内容字 {sorted(shared)[:6]}，未替换")
    _sem_record_bot(session_key, text, user_text)
    return text, []


def _record_payload_conflict(
    *,
    task_id: str | None,
    reply_id: str | None,
    seq: int,
    session: str | None,
    umo: str | None,
    kind: str,
    text: str,
    detail: str,
) -> None:
    """P0/A5：payload 冲突要落独立事件表，供 observe 计数。

    不写 yy_send_task（避免为了记冲突把原成功任务改成 failed），
    也不存完整正文/密钥；只存 sha256 + 40 字预览。
    """
    try:
        import datetime as _dt
        import hashlib
        import sqlite3

        from astrbot.core.utils.send_ledger import DEFAULT_DB

        db = os.environ.get("DEMO_LEDGER_DB") or str(DEFAULT_DB)
        con = sqlite3.connect(db, timeout=5)
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS yy_send_conflict ("
                " conflict_id TEXT PRIMARY KEY,"
                " task_id TEXT, reply_id TEXT, seq INTEGER,"
                " session TEXT, umo TEXT, kind TEXT,"
                " text_sha TEXT, text_preview TEXT,"
                " field TEXT, detail TEXT, created_at TEXT NOT NULL)"
            )
            con.execute(
                "INSERT INTO yy_send_conflict(conflict_id,task_id,reply_id,seq,"
                " session,umo,kind,text_sha,text_preview,field,detail,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    task_id,
                    reply_id,
                    int(seq),
                    session,
                    umo,
                    kind,
                    hashlib.sha256((text or "").encode()).hexdigest(),
                    None,  # 不存正文预览，避免把私聊内容写进运维表
                    "payload",
                    (detail or "")[:300],
                    _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                ),
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[发送台账] 冲突事件落库失败（不影响拒绝）: {exc}")


def _crash_hook():
    """崩溃注入钩子（测试用）。生产环境 DEMO_CRASH_AT 不设置时是空操作。"""
    def _noop(_name: str) -> None:
        return None

    try:
        from astrbot.core.utils.send_ledger import crash_point

        return crash_point
    except Exception:  # noqa: BLE001
        return _noop


def _audio_id_of(seg) -> str | None:
    """语音段的媒体标识（attachment_id / 文件路径 / URL）。"""
    if isinstance(seg, Record):
        return getattr(seg, "file", None) or getattr(seg, "url", None)
    return None


def _task_text(chain: MessageChain) -> str:
    """给发送台账用的“这条消息是什么”。"""
    parts = []
    for comp in chain.chain:
        if isinstance(comp, Plain):
            parts.append(comp.text)
        elif isinstance(comp, Record):
            # TTS 转出来的 Record 自带 text（就是机器人“说”的那句话）；没有才用占位符
            said = (getattr(comp, "text", "") or "").strip()
            parts.append(said if said else "[语音]")
        elif isinstance(comp, Image):
            parts.append("[图片]")
        elif isinstance(comp, File):
            parts.append("[文件]")
        elif isinstance(comp, Node | Nodes):
            parts.append("[转发]")
        elif isinstance(comp, At):
            parts.append(f"@{comp.name or comp.qq}")
    return " ".join(p for p in parts if p).strip() or "[非文本]"


def _task_kind(chain: MessageChain) -> str:
    """R03：按实际媒体类型记 kind，单条语音不能记成 text。"""
    if any(isinstance(c, Record) for c in chain.chain):
        return "record"
    if any(isinstance(c, Image) for c in chain.chain):
        return "image"
    if any(isinstance(c, File) for c in chain.chain):
        return "file"
    if any(isinstance(c, Video) for c in chain.chain):
        return "video"
    return "text"


def _extract_message_id(ret) -> str | None:
    if isinstance(ret, dict):
        mid = ret.get("message_id") or (ret.get("data") or {}).get("message_id")
        return str(mid) if mid is not None else None
    if isinstance(ret, int | str):
        return str(ret)
    return None


def _rewrite_demo(text: str) -> str:
    """用本地微调模型把长回复改写成Bot的短句口吻。"""
    try:
        payload = {
            "model": "demo",
            "stream": False,
            "messages": [
                {"role": "system", "content": (
                    "你是改写器。把用户的话改写成Bot说的第一人称话：极短（1-15字），"
                    "句尾可带半括号（或），用空格断句，不用句号不用emoji不换行，"
                    "主语改成我，不回应不反问，只改写原意，保留所有数字和名字。"
                    "例：原句「你要相信我的决心」→「你相信我嘛（」；"
                    "原句「你要不要出去走走」→「我们出去走走吧（」。只输出改写结果。")},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            d = _json.loads(r.read().decode())
        out = (d.get("message") or {}).get("content", "").strip().strip('"')
        if 1 <= len(out) <= 30 and out != text:
            return out
    except Exception:
        pass
    return text


class AiocqhttpMessageEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str,
        message_obj,
        platform_meta,
        session_id,
        bot: CQHttp,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.bot = bot

    @staticmethod
    async def _from_segment_to_dict(segment: BaseMessageComponent) -> dict:
        """修复部分字段"""
        if isinstance(segment, Image | Record):
            # For Image and Record segments, we convert them to base64
            bs64 = await segment.convert_to_base64()
            return {
                "type": segment.type.lower(),
                "data": {
                    "file": f"base64://{bs64}",
                },
            }
        if isinstance(segment, File):
            # For File segments, we need to handle the file differently
            d = await segment.to_dict()
            file_val = d.get("data", {}).get("file", "")
            if file_val:
                import pathlib

                try:
                    # 使用 pathlib 处理路径，能更好地处理 Windows/Linux 差异
                    path_obj = pathlib.Path(file_val)
                    # 如果是绝对路径且不包含协议头 (://)，则转换为标准的 file: URI
                    if path_obj.is_absolute() and "://" not in file_val:
                        d["data"]["file"] = path_obj.as_uri()
                except Exception:
                    # 如果不是合法路径（例如已经是特定的特殊字符串），则跳过转换
                    pass
            return d
        if isinstance(segment, Video):
            d = await segment.to_dict()
            return d
        # For other segments, we simply convert them to a dict by calling toDict
        return segment.toDict()

    @staticmethod
    async def _parse_onebot_json(message_chain: MessageChain):
        """解析成 OneBot json 格式"""
        ret = []
        for segment in message_chain.chain:
            if isinstance(segment, At):
                # At 组件后插入一个空格，避免与后续文本粘连
                d = await AiocqhttpMessageEvent._from_segment_to_dict(segment)
                ret.append(d)
                ret.append({"type": "text", "data": {"text": " "}})
            elif isinstance(segment, Plain):
                if not segment.text.strip():
                    continue
                d = await AiocqhttpMessageEvent._from_segment_to_dict(segment)
                # 去掉换行：示例对象聊天不换行，多句用空格连写
                if d.get("type") == "text" and isinstance(d.get("data", {}).get("text"), str):
                    d["data"]["text"] = d["data"]["text"].replace("\r", " ").replace("\n", " ")
                ret.append(d)
            else:
                d = await AiocqhttpMessageEvent._from_segment_to_dict(segment)
                ret.append(d)
        # 贴纸不再塞进同一条消息：由 send_message 在文字送达后单独发一条
        # （机器人的习惯是先说话、再补一张动画表情，两个气泡）
        return ret

    @classmethod
    async def _dispatch_send(
        cls,
        bot: CQHttp,
        event: Event | None,
        is_group: bool,
        session_id: str | None,
        messages: list[dict],
    ):
        # session_id 必须是纯数字字符串
        session_id_int = (
            int(session_id) if session_id and session_id.isdigit() else None
        )
        routing_params = {}
        if isinstance(event, Event) and event.get("self_id"):
            routing_params["self_id"] = event["self_id"]

        if is_group and isinstance(session_id_int, int):
            return await bot.send_group_msg(
                group_id=session_id_int,
                message=messages,
                **routing_params,
            )
        elif not is_group and isinstance(session_id_int, int):
            return await bot.send_private_msg(
                user_id=session_id_int,
                message=messages,
                **routing_params,
            )
        elif isinstance(event, Event):  # 最后兜底
            return await bot.send(event=event, message=messages)
        else:
            raise ValueError(
                f"无法发送消息：缺少有效的数字 session_id({session_id}) 或 event({event})",
            )

    @classmethod
    async def _tracked_send(cls, send_call, *, session_key: str, text: str,
                            kind: str, reply_id: str, seq: int,
                            client_token: str | None = None,
                            managed_history: bool = False,
                            umo: str | None = None,
                            history_text: str | None = None,
                            conversation_id: str | None = None,
                            audio_id: str | None = None,
                            total: int | None = None):
        """建任务 → 原子认领 → 发送 → 记账（P0-A/P0-B）。

        P0-B：发送前把 UMO、目标会话、最终历史正文一起落库；
        恢复只认任务里保存的目标，不再猜“该用户最新会话”。

        多段回复：seq 0 的任务沿用业务 token，后续段用 token:seq；
        历史只挂在最后一段，全部段 sent 后由 send_message 统一提交。
        """
        try:
            from astrbot.core.utils.send_ledger import get_ledger

            ledger = get_ledger()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[发送台账] 初始化失败，退回直发: {e}")
            return await send_call()

        # 发送前解析并保存目标会话；恢复阶段不再查询“最新会话”
        cid = conversation_id
        if cid is None and umo:
            try:
                cid = await _ledger_call(ledger.resolve_conversation, umo)
            except Exception:  # noqa: BLE001
                cid = None

        if client_token:
            task_id = client_token if seq == 0 else f"{client_token}:{seq}"
        else:
            task_id = reply_id if seq == 0 else f"{reply_id}:{seq}"

        try:
            task_id = await _ledger_call(
                ledger.new_task,
                session=session_key, kind=kind, text=text,
                conversation_id=cid, umo=umo, history_text=history_text,
                audio_id=audio_id,
                reply_id=reply_id, seq=seq, managed_history=managed_history,
                task_id=task_id, total=total,
            )
        except Exception as exc:  # noqa: BLE001
            # R03：同一个 task_id 换了正文/会话 = 载荷冲突，不能当成“重试成功”
            if exc.__class__.__name__ == "PayloadMismatch":
                _record_payload_conflict(
                    task_id=task_id, reply_id=reply_id, seq=seq,
                    session=session_key, umo=umo, kind=kind, text=text,
                    detail=str(exc),
                )
                logger.warning(f"[发送台账] 载荷冲突，拒绝复用 task={task_id[:8]}: {exc}")
                return {"state": "skipped", "error": "payload_mismatch",
                        "task_id": task_id, "reply_id": reply_id, "seq": seq,
                        "claimed": False, "resent": False}
            raise
        claimed = await _ledger_call(ledger.claim, task_id)
        if not claimed:
            state = await _ledger_call(ledger.state_of, task_id)
            # 明确失败过才允许重试；sent / unknown / skipped 一律不再发（P0-1）
            if state == "failed" and await _ledger_call(ledger.claim_retry, task_id):
                claimed = True
                logger.info(f"[发送台账] 任务 {task_id[:8]} 复用重试（第 N 次尝试）")
            else:
                logger.warning(
                    f"[发送台账] 任务 {task_id[:8]} 未认领成功，当前 state={state}，不再发送")
                return {"state": state or "unknown", "task_id": task_id,
                        "reply_id": reply_id, "seq": seq, "claimed": False, "resent": False}
        try:
            ret = await send_call()
        except Exception as exc:
            classified = await _ledger_call(ledger.finish_err, task_id, exc)
            tag = "失败(可重试)" if classified == "definite" else "结果未知(不重发)"
            logger.warning(f"[发送台账] {tag} task={task_id[:8]}: {exc}")
            raise
        # R02：平台已接受、但还没记账/写历史的崩溃注入点
        crash_point = _crash_hook()
        crash_point("after_platform_accept")
        if not await _ledger_call(ledger.finish_ok, task_id, _extract_message_id(ret)):
            # 恢复已把它判 unknown，迟到的成功响应不能覆盖终态
            state = await _ledger_call(ledger.state_of, task_id) or "unknown"
            logger.warning(f"[发送台账] 任务 {task_id[:8]} 终态已变（{state}），本次不记 sent")
            return {"state": state, "task_id": task_id, "reply_id": reply_id,
                    "seq": seq, "claimed": True, "resent": False}
        crash_point = _crash_hook()
        crash_point("after_send_confirm")
        logger.info(f"[发送台账] 已发送 task={task_id[:8]} seq={seq} kind={kind} {text[:20]!r}")
        return {"state": "sent", "task_id": task_id, "reply_id": reply_id,
                "seq": seq, "claimed": True}

    @classmethod
    async def _send_sticker_followup(
        cls,
        bot: CQHttp,
        event: Event | None,
        is_group: bool,
        session_id: str | None,
        session_key: str,
        reply_id: str,
        mode: str,
        umo: str | None,
        client_token: str | None,
        reply_text: str,
        user_text: str = "",
    ) -> None:
        """文字/语音送达后，按“情绪 + 场景”补一张机器人的动画表情。"""
        # 关系/走向类问题不配图：就算场景被判成示例（机器人选择躲闪），
        # 「我们到底是什么关系 以后怎么办」后面跟一张表情也太不像人。
        # 可用 DEMO_STICKER_AVOID_DECISION=0 关掉。
        if (user_text
                and os.environ.get("DEMO_STICKER_AVOID_DECISION", "1") != "0"):
            try:
                from astrbot.core.utils.style_profile import (
                    DECISION_RE as _DEC, RELATION_QUERY_RE as _REL)

                if _REL.search(user_text) and _DEC.search(user_text):
                    logger.info("[贴纸] 关系/走向话题，不补图")
                    return
            except Exception:  # noqa: BLE001
                pass
        if not _should_sticker(mode, reply_text):
            return
        entry = _pick_sticker(mode, user_text, reply_text, umo)
        if not entry:
            return
        seg = _sticker_segment(entry)
        if not seg:
            return
        _q_ok, _q_mult = _sticker_quota_state()
        if not _q_ok:
            logger.info("[贴纸] 已达 2 倍日预算，今天不再发")
            return
        if _q_mult < 1.0 and random.random() > _q_mult:
            logger.info(f"[贴纸] 超出日预算，降频跳过（{_q_mult:.0%} 概率保留）")
            return
        token = f"{client_token or reply_id}:st"
        try:
            await asyncio.sleep(random.uniform(0.6, 1.4))
            res = await cls._tracked_send(
                lambda: cls._dispatch_send(bot, event, is_group, session_id, [seg]),
                session_key=session_key,
                text="[动画表情]",
                kind="sticker",
                reply_id=reply_id,
                seq=99,
                client_token=token,
                managed_history=False,
                umo=umo,
                history_text=None,
            )
            logger.info(
                f"[贴纸] {str(entry.get('md5'))[:8]} mode={mode} "
                f"state={(res or {}).get('state')}")
            if isinstance(res, dict) and res.get("state") == "sent":
                _sticker_quota_consume()
            if isinstance(res, dict) and res.get("state") == "sent" and event is not None:
                try:
                    event.set_extra("_sticker_sent", True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.info(f"[贴纸] 跳过: {e}")

    @classmethod
    async def _send_multi_bubble(
        cls,
        owner,
        bot,
        raw_event,
        is_group: bool,
        session_id: str,
        token: str,
        mode: str,
        reply_text: str,
        text_only: bool,
    ) -> None:
        """Q2：被动回复偶尔补一条短气泡，更接近机器人连发的节奏。"""
        if os.environ.get("DEMO_TEST") == "1":
            return
        if not text_only or mode not in ("banter", "support"):
            return
        if len(reply_text or "") < 8:
            return
        try:
            if owner.get_extra("_sticker_sent") or owner.get_extra("_multi_bubble_sent"):
                return
            rate = float(os.environ.get("DEMO_MULTI_BUBBLE_RATE", "0.15"))
            if random.random() >= rate:
                return
            pool = (["嗯嗯", "好耶", "真的", "好家伙"] if mode == "banter"
                    else ["嗯嗯", "好耶", "知道啦"])
            follow = random.choice(pool)
            await asyncio.sleep(random.uniform(0.4, 0.9))
            res = await cls.send_message(
                bot=bot,
                message_chain=MessageChain([Plain(follow)]),
                event=raw_event,
                is_group=is_group,
                session_id=session_id,
                client_token=f"{token}:b2",
                managed_history=False,
                umo=owner.unified_msg_origin,
                style_mode=mode,
                allow_sticker=False,
            )
            if isinstance(res, dict) and res.get("state") == "sent":
                owner.set_extra("_multi_bubble_sent", True)
                logger.info(f"[多气泡] +{follow!r}")
        except Exception as e:  # noqa: BLE001
            logger.info(f"[多气泡] 跳过: {e}")

    @classmethod
    async def send_message(
        cls,
        bot: CQHttp,
        message_chain: MessageChain,
        event: Event | None = None,
        is_group: bool = False,
        session_id: str | None = None,
        client_token: str | None = None,
        managed_history: bool = False,
        umo: str | None = None,
        history_text: str | None = None,
        style_mode: str | None = None,
        allow_sticker: bool = False,
        user_text: str = "",
    ) -> dict | None:
        """发送消息至 QQ 协议端（aiocqhttp）。

        Args:
            bot (CQHttp): aiocqhttp 机器人实例
            message_chain (MessageChain): 要发送的消息链
            event (Event | None, optional): aiocqhttp 事件对象.
            is_group (bool, optional): 是否为群消息.
            session_id (str | None, optional): 会话 ID（群号或 QQ 号

        """
        # 重试/复用判定：同一 client_token 已经有任务记录（failed/unknown/sent），
        # 正文必须**逐字不变**——否则会被台账的载荷冲突拦下（R03），
        # 重试就永远发不出去。所以这类请求跳过会改文本的守门/改写/节奏。
        _prev_task = None
        if client_token:
            try:
                from astrbot.core.utils.send_ledger import get_ledger

                _prev_task = get_ledger().get(client_token)
            except Exception:  # noqa: BLE001
                _prev_task = None
        is_retry_or_replay = _prev_task is not None
        # A3/R03：首次发送若被守门改写过正文，重试必须复用台账里的同一正文，
        # 否则会变成 payload 冲突，永远卡在 failed。
        if _prev_task and _prev_task.get("text"):
            _plains = [c for c in message_chain.chain if isinstance(c, Plain)]
            if len(_plains) == 1:
                _plains[0].text = str(_prev_task["text"])

        # 场景模式：被动回复用用户那句话判定（send() 传入），主动/接口按回复文本判定
        mode = style_mode or _detect_mode(_task_text(message_chain))
        # 回复节奏：绝大多数 2~20 秒，约一成 20~45 秒；主动消息不拖。
        # 只延迟，不丢弃——无论多久都必须回。
        try:
            total_len = sum(len(c.text) for c in message_chain.chain if isinstance(c, Plain))
            if managed_history or is_retry_or_replay:
                delay = 0.0
            else:
                base = min(8.0, 2.0 + total_len / 6.0)
                r = random.random()
                if r < 0.15:
                    factor = random.uniform(0.3, 0.7)
                elif r < 0.60:
                    factor = random.uniform(0.9, 2.5)
                elif r < 0.90:
                    factor = random.uniform(2.5, 6.0)
                else:
                    factor = random.uniform(6.0, 12.0)
                delay = min(90.0, base * factor)
            if delay > 0:
                logger.info(f"[回复节奏] 延迟 {delay:.1f}s（正文 {total_len} 字）")
                _log_pacing(delay, total_len, bot)
                await asyncio.sleep(delay)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[回复节奏] 延迟失败已忽略: {e}")
        # 出站质量门禁（P0-6）：去 emoji / 括号配对 / 长度 / 场景化括号预算
        gate_key = f"{'g' if is_group else 'p'}:{session_id}"
        for comp in (message_chain.chain if not is_retry_or_replay else []):
            if isinstance(comp, Plain):
                try:
                    cleaned, acts = _apply_gate(
                        comp.text, gate_key, dedupe=False, record=False, mode=mode)
                    if acts:
                        logger.info(f"[出站门禁] {acts} | {comp.text[:30]!r} -> {cleaned[:30]!r}")
                    comp.text = cleaned
                    comp.text, ha = _vary_haohao(comp.text, mode)
                    if ha:
                        logger.info(f"[口癖] {ha} | {comp.text[:24]!r}")
                    # Q1：当前短句已偏高，不再主动收短；保留函数供单测/回滚。
                    comp.text, dec = _reinforce_decision(user_text, comp.text)
                    if dec:
                        logger.info(f"[决策] {dec}")
                    comp.text, lh = _laugh_nudge(comp.text, user_text, mode)
                    if lh:
                        logger.info(f"[哈哈] {lh} | {comp.text[:24]!r}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[出站门禁] 异常已忽略: {e}")
        if any(isinstance(c, Plain) and not c.text.strip() for c in message_chain.chain):
            message_chain.chain = [
                c for c in message_chain.chain
                if not (isinstance(c, Plain) and not c.text.strip())
            ]
        if not message_chain.chain:
            # 模型空回复/全是标点：不能静默，给一句最短兜底
            logger.info("[空回复] 清洗后无内容，发兜底「在的（」")
            message_chain.chain = [Plain("在的（")]
        # 跨轮语义去重 + 相关性守门（元话题泄漏）
        _sem_record_user(gate_key, user_text)
        for comp in (message_chain.chain if not is_retry_or_replay else []):
            if isinstance(comp, Plain):
                new, acts = _semantic_guard(comp.text, user_text, gate_key, mode)
                if acts:
                    logger.info(f"[语义守门] {acts} | {comp.text[:24]!r} -> {new[:24]!r}")
                comp.text = new
        # 语气重写：只改写 11~16 字的中等回复；默认关闭（DEMO_REWRITE=1 才开）
        for comp in (message_chain.chain if (_REWRITE_ENABLED and not is_retry_or_replay) else []):
            if (
                isinstance(comp, Plain)
                and 11 <= len(comp.text) <= 16
                and not _REWRITE_SKIP.search(comp.text)
            ):
                try:
                    comp.text = await asyncio.to_thread(_rewrite_demo, comp.text)
                except Exception:
                    pass
        # 改写后再过一次轻量清洗（不在这里去重：A01/A04 的测试会重复发同一 token，
        # 去重会绕过台账的“已发送就不再发”语义；被动去重放在 send() 里）
        for comp in message_chain.chain:
            if isinstance(comp, Plain):
                try:
                    comp.text, acts = _apply_gate(
                        comp.text, gate_key, dedupe=False, record=False,
                        mode=mode, quirk=False)
                    if acts:
                        logger.info(f"[出站门禁·改写后] {acts}")
                except Exception:
                    pass
        if any(isinstance(c, Plain) and not c.text.strip() for c in message_chain.chain):
            message_chain.chain = [
                c for c in message_chain.chain
                if not (isinstance(c, Plain) and not c.text.strip())
            ]
        if not message_chain.chain:
            return
        # 转发消息、文件消息不能和普通消息混在一起发送
        send_one_by_one = any(
            isinstance(seg, Node | Nodes | File) for seg in message_chain.chain
        )
        # P0-B：业务回复的父 ID 由调用方给（管家重试复用），没有才新建。
        # 多段的 task_id 由父 ID + seq 生成，不能共用同一个 task。
        reply_id = client_token or uuid.uuid4().hex
        try:
            from astrbot.core.utils.send_ledger import get_ledger

            ledger = get_ledger()
        except Exception:  # noqa: BLE001
            ledger = None
        # 同一业务 token 已经 sent：单条消息直接返回；多段消息必须先确认
        # yy_turn 清单已经整轮齐，不能只看 seq0 就冒充整轮成功。
        if client_token and ledger is not None:
            try:
                _existing = await _ledger_call(ledger.get, client_token)
            except Exception:  # noqa: BLE001
                _existing = None
            if _existing and _existing.get("state") == "sent":
                if not send_one_by_one:
                    return {"state": "sent", "task_id": client_token,
                            "reply_id": reply_id, "seq": 0,
                            "claimed": False, "resent": False, "already": True}
                try:
                    _prep = await _ledger_call(ledger.prepare_turn_commit, reply_id)
                except Exception:  # noqa: BLE001
                    _prep = {}
                if _prep.get("status") in ("ready", "already"):
                    _tid = _prep.get("task_id") or client_token
                    if _prep.get("status") == "ready" and umo:
                        await cls._commit_history(ledger, _tid, umo, history_text)
                    return {"state": "sent", "task_id": _tid,
                            "reply_id": reply_id, "seq": 0,
                            "claimed": False, "resent": False, "already": True}
                # partial：不提前返回，继续走段循环；已 sent 的段会在循环里跳过。
        if not send_one_by_one:
            ret = await cls._parse_onebot_json(message_chain)
            if not ret:
                return
            audio_id = next(
                (_audio_id_of(c) for c in message_chain.chain if _audio_id_of(c)), None)
            # R03：单条消息也要按实际媒体类型记账，不能一律记 text。
            res = await cls._tracked_send(
                lambda: cls._dispatch_send(bot, event, is_group, session_id, ret),
                session_key=gate_key,
                text=_task_text(message_chain),
                kind=_task_kind(message_chain),
                reply_id=reply_id,
                seq=0,
                client_token=client_token,
                managed_history=managed_history,
                umo=umo,
                history_text=history_text,
                audio_id=audio_id,
                total=1 if managed_history else None,
            )
            if (isinstance(res, dict) and res.get("state") == "sent"
                    and res.get("claimed")):
                # A08：只有确认送达（且是本次真正发出）才提交一次风格统计
                for comp in message_chain.chain:
                    if isinstance(comp, Plain):
                        try:
                            _commit_style(comp.text, gate_key, mode)
                        except Exception:  # noqa: BLE001
                            pass
            if (ledger is not None and managed_history and umo
                    and isinstance(res, dict) and res.get("state") == "sent"):
                await cls._commit_history(ledger, res.get("task_id"), umo, history_text)
                cls._activate_commitment(umo, history_text, res.get("task_id"))
            # 被动回复/主动消息：文字或 TTS 语音送达后按场景补一张贴纸。
            # allow_sticker 默认 False，测试和内部 send_message 调用不会被贴纸干扰。
            if (allow_sticker and isinstance(res, dict) and res.get("state") == "sent"
                    and all(isinstance(c, (Plain, Record)) for c in message_chain.chain)):
                await cls._send_sticker_followup(
                    bot, event, is_group, session_id, gate_key, reply_id,
                    mode, umo, client_token, _task_text(message_chain),
                    user_text=user_text)
            return res
        segments = list(message_chain.chain)
        # 气泡上限：一轮最多 3 条。机器人的真实分布：1 条 62%、2 条 27%、3 条 7%、4+ 4%。
        if len(segments) > 3:
            tail = segments[2:]
            if all(isinstance(c, Plain) for c in tail):
                merged = " ".join(c.text.strip() for c in tail if c.text.strip())
                if merged:
                    segments = [segments[0], segments[1], Plain(merged)]
                    logger.info(f"[气泡] {len(message_chain.chain)} 段合并为 3 段")
        crash_point = _crash_hook()
        states: list[dict] = []
        # R02：第一段发送前落库"这一轮本来有几段"，崩溃后才知道缺哪段。
        # R2：清单拒绝/落库失败必须在任何网络发送前终止，不能只记日志继续。
        if ledger is not None and managed_history and umo and len(segments) > 1:
            try:
                ok = await _ledger_call(
                    ledger.start_turn, reply_id=reply_id, session=gate_key,
                    total=len(segments), umo=umo, history_text=history_text)
                if not ok:
                    raise ValueError("turn manifest conflict")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[发送台账] 整轮清单落库失败 reply={str(reply_id)[:8]}: {e}")
                return {"state": "skipped", "error": "turn_manifest_conflict",
                        "task_id": None, "reply_id": reply_id, "seq": 0,
                        "claimed": False, "resent": False}
        for i, seg in enumerate(segments):
            # 多段重试：seq0/seqN 已经 sent 的段直接跳过，不能因为 seq0
            # 已存在就把整轮当成 sent（P0-2）。
            if client_token and ledger is not None:
                _seg_tid = client_token if i == 0 else f"{client_token}:{i}"
                try:
                    _seg_row = await _ledger_call(ledger.get, _seg_tid)
                except Exception:  # noqa: BLE001
                    _seg_row = None
                if _seg_row and _seg_row.get("state") == "sent":
                    states.append({"state": "sent", "task_id": _seg_tid,
                                   "reply_id": reply_id, "seq": i,
                                   "claimed": False, "resent": False})
                    continue
            # R02：每段发送前的崩溃注入点（生产环境不设 DEMO_CRASH_AT 时为 no-op）
            crash_point("before_segment_send")
            if isinstance(seg, Node | Nodes):
                # 合并转发消息
                if isinstance(seg, Node):
                    nodes = Nodes([seg])
                    seg = nodes

                payload = await seg.to_dict()

                if is_group:
                    payload["group_id"] = session_id
                    if isinstance(event, Event) and event.get("self_id"):
                        payload["self_id"] = event["self_id"]
                    r = await cls._tracked_send(
                        lambda p=payload: bot.call_action("send_group_forward_msg", **p),
                        session_key=gate_key, text=_task_text(message_chain),
                        kind="forward", reply_id=reply_id, seq=i,
                        client_token=client_token, managed_history=False,
                        umo=umo, history_text=None,
                    )
                    states.append(r or {"state": "unknown"})
                else:
                    payload["user_id"] = session_id
                    if isinstance(event, Event) and event.get("self_id"):
                        payload["self_id"] = event["self_id"]
                    r = await cls._tracked_send(
                        lambda p=payload: bot.call_action("send_private_forward_msg", **p),
                        session_key=gate_key, text=_task_text(message_chain),
                        kind="forward", reply_id=reply_id, seq=i,
                        client_token=client_token, managed_history=False,
                        umo=umo, history_text=None,
                    )
                    states.append(r or {"state": "unknown"})
            elif isinstance(seg, File):
                d = await cls._from_segment_to_dict(seg)
                r = await cls._tracked_send(
                    lambda dd=d: cls._dispatch_send(bot, event, is_group, session_id, [dd]),
                    session_key=gate_key, text="[文件]",
                    kind="file", reply_id=reply_id, seq=i,
                    client_token=client_token, managed_history=False,
                    umo=umo, history_text=None,
                )
                states.append(r or {"state": "unknown"})
            else:
                messages = await cls._parse_onebot_json(MessageChain([seg]))
                if not messages:
                    continue
                r = await cls._tracked_send(
                    lambda m=messages: cls._dispatch_send(bot, event, is_group, session_id, m),
                    session_key=gate_key,
                    text=_task_text(MessageChain([seg])),
                    kind="segment",
                    reply_id=reply_id,
                    seq=i,
                    client_token=client_token, managed_history=False,
                    umo=umo, history_text=None,
                    audio_id=_audio_id_of(seg),
                )
                if (isinstance(r, dict) and r.get("state") == "sent"
                        and r.get("claimed") and isinstance(seg, Plain)):
                    try:
                        _commit_style(seg.text, gate_key, mode)
                    except Exception:  # noqa: BLE001
                        pass
                states.append(r or {"state": "unknown"})
                await asyncio.sleep(0.5)
        # R02：整轮是否齐由 yy_turn.total 决定，不再靠"库里碰巧有几个兄弟段"
        if ledger is not None and managed_history and umo and states:
            try:
                prep = await _ledger_call(ledger.prepare_turn_commit, reply_id)
                if prep.get("status") == "ready" and prep.get("task_id"):
                    res = await cls._commit_history(
                        ledger, prep["task_id"], umo, history_text)
                    if res in ("committed", "already", "skipped_no_conv"):
                        await _ledger_call(ledger.mark_turn_committed, reply_id)
                        cls._activate_commitment(umo, history_text, prep["task_id"])
                elif prep.get("status") == "partial":
                    logger.info(f"[发送台账] 整轮未齐，暂不写历史 reply={reply_id[:8]}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[发送台账] 整轮提交失败 reply={reply_id[:8]}: {e}")
        # 贴纸放在历史提交**之后**：历史先落，贴纸的延迟/失败不影响台账。
        if allow_sticker and any(
                isinstance(x, dict) and x.get("state") == "sent" for x in states):
            try:
                await cls._send_sticker_followup(
                    bot, event, is_group, session_id, gate_key, reply_id, mode,
                    umo, client_token, _task_text(message_chain),
                    user_text=user_text)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[贴纸] 多气泡补图失败（不影响已发送）: {e}")
        return _worst_state(states)

    @classmethod
    def _activate_commitment(cls, umo: str | None, text: str | None,
                             task_id: str | None) -> None:
        """R08：消息真的发出去之后，才把对应承诺从 planned 转 active。"""
        if not umo or not text or not task_id:
            return
        try:
            from astrbot.core.utils import life_state

            if not life_state.enabled():
                return
            if life_state.activate_from_send(umo, text, task_id):
                logger.info(f"[生活层] 承诺已激活 task={str(task_id)[:8]}")
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    async def _commit_history(cls, ledger, task_id: str | None, umo: str,
                              history_text: str | None = None) -> str:
        """发送成功后统一提交历史；和被动流程共用同一会话锁。"""
        if not task_id:
            return "missing_task"
        try:
            from astrbot.core.utils.session_lock import session_lock_manager

            async with session_lock_manager.acquire_lock(umo):
                res = await _ledger_call(ledger.commit_history, task_id)
            logger.info(f"[发送台账] 历史提交 task={task_id[:8]}: {res}")
            if res in ("committed", "already"):
                _row = await _ledger_call(ledger.get, task_id)
                text = history_text or (_row or {}).get("history_text") or ""
                if text:
                    try:
                        await _ledger_call(
                            ledger.recent_push, f"p:{umo}", text, scope="proactive")
                    except Exception:  # noqa: BLE001
                        pass
            return res
        except Exception as e:  # noqa: BLE001
            # 历史没写进去不影响已经发出去这个事实；周期恢复会补
            logger.error(f"[发送台账] 历史提交失败 task={str(task_id)[:8]}: {e}")
            return "error"

    async def send(self, message: MessageChain) -> None:
        """发送消息。

        P0-B：被动回复也生成一个 delivery token，并把发送结果挂到事件上；
        internal._save_to_history 据此决定要不要把这条 assistant 写进历史，
        避免“没发出去的话”变成机器人记得自己说过。
        """
        event = getattr(self.message_obj, "raw_message", None)
        # 运维巡检/测试消息不走 QQ：它们不是机器人，会污染配置和历史。
        try:
            _ops_text = " ".join(
                c.text for c in message.chain if isinstance(c, Plain) and c.text).strip()
            if re.match(r"^\s*[（(]巡检[）)]|^\s*巡查[）)]", _ops_text):
                logger.warning(f"[巡检拦截] 运维消息不进 QQ: {_ops_text[:40]!r}")
                return
            if _ops_text in ("测试一条消息", "测试消息"):
                logger.warning(f"[巡检拦截] 测试消息不进 QQ: {_ops_text!r}")
                return
        except Exception:  # noqa: BLE001
            pass

        is_group = bool(self.get_group_id())
        session_id = self.get_group_id() if is_group else self.get_sender_id()
        gate_key = f"{'g' if is_group else 'p'}:{session_id}"
        token = uuid.uuid4().hex
        self.set_extra("delivery_token", token)
        # 分段回复会多次调用 send()；一轮最多补一张贴纸，别每段都补。
        send_count = int(self.get_extra("_turn_send_count") or 0)
        allow_sticker = send_count == 0
        if send_count == 0:
            # 本轮第一次 send：清掉上一轮残留的送达文本/任务列表。
            self.set_extra("delivery_text", "")
            self.set_extra("delivery_tokens", [])
        self.set_extra("_turn_send_count", send_count + 1)
        # 用用户这句话判场景：日常/被撩/安慰/认真，决定括号、长度和要不要贴纸
        style_mode = _detect_mode(self.message_str)
        plain_texts = [
            c.text.strip() for c in message.chain
            if isinstance(c, Plain) and c.text.strip()
        ]
        voice_texts = [
            str(getattr(c, "text", "")).strip() for c in message.chain
            if isinstance(c, Record) and str(getattr(c, "text", "")).strip()
        ]
        reply_text = " ".join(plain_texts)
        text_only = bool(reply_text) and all(isinstance(c, Plain) for c in message.chain)
        # 纯文字/纯语音都参与去重；TTS 以前绕过去重，导致同一句反复发。
        _dedupe_text = reply_text or " ".join(voice_texts)
        _dedupe_ok = bool(_dedupe_text) and all(
            isinstance(c, (Plain, Record)) for c in message.chain)
        # 被动去重：同一句话 45 分钟内不再发。
        # 不静默：命中后改发一句“刚说过了”，用户至少能得到回应。
        if _dedupe_ok and _passive_duplicate(_dedupe_text, self.unified_msg_origin):
            # 命中说明原句已经在 45 分钟窗口里；先把原句重新记一次，
            # 否则下面把 reply_text 换成兜底句后，原句永远进不了去重表。
            _passive_mark_sent(_dedupe_text, self.unified_msg_origin)
            fallback = _duplicate_fallback(style_mode)
            logger.info(
                f"[被动去重] 与最近回复重复，改兜底: {_dedupe_text[:24]!r} -> {fallback!r}")
            message = MessageChain([Plain(fallback)])
            reply_text = fallback
            _dedupe_text = fallback
        try:
            result = await self.send_message(
                bot=self.bot,
                message_chain=message,
                event=event,  # 不强制要求一定是 Event
                is_group=is_group,
                session_id=session_id,
                client_token=token,
                umo=self.unified_msg_origin,
                style_mode=style_mode,
                allow_sticker=allow_sticker,
                user_text=getattr(self, "message_str", "") or "",
            )
            state = result.get("state") if isinstance(result, dict) else None
            self.set_extra("delivery_state", state or "empty")
            if state == "sent":
                # P0：只在确认 sent 后登记被动去重，失败/未知不占用 45 分钟窗口。
                if _dedupe_ok and _dedupe_text:
                    _passive_mark_sent(_dedupe_text, self.unified_msg_origin)
                # P0-5：被动回复真的说出生活事件时，也登记 told（不是注入就算）。
                try:
                    from astrbot.core.utils.life_state import commit_told as _commit_told

                    _inbound_mid = str(getattr(
                        getattr(self, "message_obj", None), "message_id", "") or "")
                    _commit_told(
                        self.unified_msg_origin,
                        _dedupe_text or reply_text,
                        task_id=str(result.get("task_id") or "") or None,
                        message_id=(_inbound_mid
                                    or str(result.get("message_id") or "") or None))
                except Exception:  # noqa: BLE001
                    pass
                # R04：把"实际送达/念出的那句"挂到事件上，历史按它写而不是按模型原稿。
                # 分段回复会多次调用 send()，必须累加，不能只留最后一段。
                try:
                    _new = _task_text(message).strip()
                    _old = str(self.get_extra("delivery_text") or "").strip()
                    self.set_extra("delivery_text",
                                   (_old + " " + _new).strip() if _old else _new)
                    _toks = list(self.get_extra("delivery_tokens") or [])
                    _tid = result.get("task_id") if isinstance(result, dict) else None
                    if _tid:
                        _toks.append(str(_tid))
                    self.set_extra("delivery_tokens", _toks)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:
            self.set_extra("delivery_state", "failed")
            raise
        await super().send(message)
        try:
            if isinstance(result, dict) and result.get("state") == "sent":
                await cls._send_multi_bubble(
                    self, self.bot, event, is_group, session_id, token,
                    mode, reply_text, text_only)
        except Exception:  # noqa: BLE001
            pass

    async def send_streaming(
        self,
        generator: AsyncGenerator,
        use_fallback: bool = False,
    ):
        if not use_fallback:
            buffer = None
            async for chain in generator:
                if not buffer:
                    buffer = chain
                else:
                    buffer.chain.extend(chain.chain)
            if not buffer:
                return None
            buffer.squash_plain()
            await self.send(buffer)
            return await super().send_streaming(generator, use_fallback)

        buffer = ""
        pattern = re.compile(r"[^。？！~…]+[。？！~…]+")

        async for chain in generator:
            if isinstance(chain, MessageChain):
                for comp in chain.chain:
                    if isinstance(comp, Plain):
                        buffer += comp.text
                        if any(p in buffer for p in "。？！~…"):
                            buffer = await self.process_buffer(buffer, pattern)
                    else:
                        await self.send(MessageChain(chain=[comp]))
                        await asyncio.sleep(1.5)  # 限速

        buffer = buffer.strip()
        if buffer:
            await self.send(MessageChain([Plain(buffer)]))
        return await super().send_streaming(generator, use_fallback)

    async def get_group(self, group_id=None, **kwargs):
        """Get OneBot group details while preserving inbound data on failures.

        Args:
            group_id: Optional OneBot group identifier.
            **kwargs: Reserved compatibility arguments.

        Returns:
            Enriched group information, or a basic group when an API is unavailable.
        """
        resolved_group_id = group_id or self.get_group_id()
        if not resolved_group_id:
            return None
        resolved_group_id = str(resolved_group_id)
        api_group_id = (
            int(resolved_group_id) if resolved_group_id.isdigit() else resolved_group_id
        )

        current_group = self.message_obj.group
        group = (
            current_group
            if current_group and current_group.group_id == resolved_group_id
            else Group(group_id=resolved_group_id)
        )

        routing_params = {}
        if getattr(self.message_obj, "self_id", None):
            routing_params["self_id"] = self.message_obj.self_id

        try:
            info = await self.bot.call_action(
                "get_group_info",
                group_id=api_group_id,
                **routing_params,
            )
            if isinstance(info, dict):
                group.group_name = info.get("group_name") or group.group_name
                member_count = info.get("member_count")
                if member_count is not None:
                    try:
                        group.member_count = int(member_count)
                    except (TypeError, ValueError):
                        logger.warning(
                            "[aiocqhttp] Invalid member_count for group %s",
                            resolved_group_id,
                        )
        except Exception as exc:
            logger.warning(
                "[aiocqhttp] Failed to get group information for %s: %s",
                resolved_group_id,
                exc,
            )

        try:
            members = await self.bot.call_action(
                "get_group_member_list",
                group_id=api_group_id,
                **routing_params,
            )
        except Exception as exc:
            logger.warning(
                "[aiocqhttp] Failed to get members for group %s: %s",
                resolved_group_id,
                exc,
            )
            return group
        if not isinstance(members, list):
            return group

        owner_id = None
        admin_ids: list[str] = []
        for member in members:
            if not isinstance(member, dict) or member.get("user_id") is None:
                continue
            if member.get("role") == "owner":
                owner_id = str(member["user_id"])
            if member.get("role") == "admin":
                admin_ids.append(str(member["user_id"]))

        group.group_admins = admin_ids
        group.group_owner = owner_id
        group.members = [
            MessageMember(
                user_id=str(member["user_id"]),
                nickname=member.get("nickname") or member.get("card"),
            )
            for member in members
            if isinstance(member, dict) and member.get("user_id") is not None
        ]
        if group.member_count is None:
            group.member_count = len(group.members)
        return group
