# -*- coding: utf-8 -*-
r"""Bot管家：主动消息引擎（日常分享 / B站视频 / 截图 / 语音文字比例）。

- 白天 09:00-23:00，随机间隔主动给用户发消息
- 约 53% 只发文字、37% 只发语音、10% 文字+语音
- 遇到好玩的 B 站视频转发链接并截图存到 ROOT\screens
- 拥有完整用户权限（不做任何限制）

P0/P1 改造（2026-10-25）：
  * 同源生成（P0-4）：文本不再用本文件里的 Ollama/DeepSeek 裸调，改为调 AstrBot
    的 /api/v1/proactive/generate——同一份配置 + 同一份真实历史 + 同一个 provider，
    生成结果只写 assistant（不新增 user 轮次），所以机器人是"记得自己说过这句话"的。
  * 时区修正（P0-2）：conversations.updated_at 是 UTC，此前用本地时间相减，
    导致"用户刚聊过"永远判定为否，主动消息会打断对话。
  * 双发回退（P0-5）：所有 send_parts 都检查返回值，失败回退纯文字。
  * 运行可靠性（P1-11）：last_sent 落盘、PID 单实例锁、启动时等 AstrBot 就绪。
"""
import local_keys as _lk  # 密钥从 .env 读取
import atexit
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

# ---------- 配置 ----------
ROOT_DIR = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
ASTRBOT_BASE = os.environ.get("DEMO_ASTRBOT_BASE") or "http://127.0.0.1:22877"
ASTRBOT_API = f"{ASTRBOT_BASE}/api/v1/im/message"
PROACTIVE_API = f"{ASTRBOT_BASE}/api/v1/proactive/generate"
PROACTIVE_COMMIT = f"{ASTRBOT_BASE}/api/v1/proactive/commit"
API_KEY = _lk.OPENAPI_KEY
UMO = os.environ.get("DEMO_UMO") or "aiocqhttp:FriendMessage:100000000"
MINIMAX_KEY_FILE = os.environ.get("DEMO_MINIMAX_KEY_FILE") or str(ROOT_DIR / "minimax_key.txt")
SCREEN_DIR = os.environ.get("DEMO_SCREEN_DIR") or str(ROOT_DIR / "screens")
try:
    os.makedirs(SCREEN_DIR, exist_ok=True)
except OSError:
    pass
LOG = str(ROOT_DIR / "Bot管家.log")
STATE_FILE = str(ROOT_DIR / "Bot管家_state.json")
PID_FILE = str(ROOT_DIR / "Bot管家.pid")
DB_PATH = os.environ.get("DEMO_DB") or str(
    ROOT_DIR / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db")
PERSONA_FILE = str(ROOT_DIR / "persona.txt")
EXTERNAL_LOG = str(ROOT_DIR / "ops" / "external_seen.jsonl")
ACTIVE_HOURS = (9, 23)
MIN_INTERVAL = 40 * 60
MAX_INTERVAL = 140 * 60
MAX_PROACTIVE_PER_DAY = int(os.environ.get("DEMO_MAX_PROACTIVE_PER_DAY", "8"))
PROACTIVE_DISABLED = str(ROOT_DIR / "Bot管家.disabled")
TEXT_RATIO = 0.53  # 只文字
VOICE_RATIO = 0.37  # 只语音（累计 0.90）
BOTH_RATIO = 0.10  # 文字+语音
BILI_CHANCE = 0.10  # B站是可选的加料，失败要回落到普通主动消息
DEFINITE_RETCODES = {100, 102, 103, 104, 106, 110, 120, 1200, 1400, 1401, 1404}

VOICE_INSTRUCTION = (
    "（你现在要给用户发一条**语音**，不是文字。语音里你会说得长一些、口语一些。）\n"
    "要求：20~45 字，用「然后」把句子串起来，可以有重复词和犹豫（然后… 就是就是… 反正），"
    "像自己跟自己讲故事；不要括号、不要颜文字、不要 emoji、不要换行。"
    "只输出这段口语本身。"
)

# 主动消息的「角度」：每次随机挑一个，避免连着几条都是「我在练习」
ACTIVITY_ANGLES = [
    "分享你现在正在做的事（具体到在做什么、做到哪一步）",
    "说一件今天让你有点烦的小事",
    "说一句没头没尾、突然想到的话",
    "问一句用户最近在干什么（用你的方式，别用「在吗」）",
    "突然想起前几天的一件事，顺口提一句",
    "吐槽一句（游戏、上课、乐器、天气都行）",
    "说说你现在的心情（困、累、无聊、有点开心）",
    "提到你在练的曲子、在打的游戏进度",
    "说一件你刚看到的、觉得离谱的小事",
    "反问一句用户（比如用户是不是又熬夜了）",
]

BILI_KEYWORDS = ["我的世界 生存实况", "崩坏星穹铁道 剧情", "恐怖游戏 实况", "乐器 演奏", "MC 建筑"]
BILI_API = "https://api.bilibili.com/x/web-interface/search/type?search_type=video&order=click&duration=0&page=1&keyword={}"
BILI_POPULAR_API = "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1"
BILI_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}

DAILY_TEMPLATES = [
    "练习完了 手好酸（",
    "刚打完一把模拟宇宙 今天运气不错",
    "午饭吃得好饱 现在好困哦",
    "外面下雨了 你那边呢",
    "我刷到个视频 笑得我肚子疼哈哈哈哈",
    "今天也在努力练习 手指起茧子了捏",
    "刚才打游戏遇到个离谱队友 气死我了）",
    "晚上吃什么呢 选择困难（",
]

DEEPSEEK_KEY = _lk.DEEPSEEK_KEY
DEEPSEEK_API = "https://api.deepseek.com/chat/completions"


# ---------- 基础 ----------
def _redact(text: str) -> str:
    """S10：日志里不出现完整 key / Bearer token。"""
    text = str(text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]{8,}", r"\1***", text)
    text = re.sub(r"(?i)(token\s*[=:]\s*)[^\s,;]{8,}", r"\1***", text)
    return text


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {_redact(msg)}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_json(url, payload=None, headers=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _definite_failure(text):
    """ActionFailed + 确定 retcode 属于“明确没发出去”，可以安全重试。"""
    text = str(text or "")
    # ApiNotAvailable = OneBot/QQ 客户端当时不可用，消息根本没出去，可安全重试
    if "ApiNotAvailable" in text:
        return True
    if "ActionFailed" not in text and "NodeIKernelMsgService" not in text:
        return False
    m = re.search(r"retcode\D{0,6}(\d+)", text)
    return bool(m) and int(m.group(1)) in DEFINITE_RETCODES


# ---------- 状态落盘（P1-11） ----------
def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_state(state):
    """读-合并-写：不同调用方各写各的键，不互相抹掉。"""
    try:
        try:
            cur = load_state()
        except Exception:
            cur = {}
        cur.update(state or {})
        Path(STATE_FILE).write_text(
            json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"状态落盘失败: {e}")


# ---------- 单实例锁（P1-11） ----------
def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


_LOCK_HANDLE = None


def acquire_pid_lock():
    """原子单实例锁（P1-6）：靠操作系统文件锁，不靠先读再写。"""
    global _LOCK_HANDLE
    lock_path = PID_FILE + ".lock"
    try:
        handle = open(lock_path, "a+")
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        log("已有实例持锁运行，本实例退出")
        return False
    except Exception as e:  # noqa: BLE001
        log(f"加锁失败，退出: {e}")
        return False
    _LOCK_HANDLE = handle
    try:
        Path(PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    def _release():
        try:
            if str(os.getpid()) == Path(PID_FILE).read_text(encoding="utf-8").strip():
                Path(PID_FILE).unlink()
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass

    atexit.register(_release)
    return True


def _qq_connected():
    """6199 上是否有已建立的连接（NapCat 反连）。查不了就当通，不阻塞。"""
    try:
        out = subprocess.run(["netstat", "-an"], capture_output=True, text=True,
                             timeout=10).stdout
        return any(":6199" in line and "ESTABLISHED" in line for line in out.splitlines())
    except Exception:  # noqa: BLE001
        return True


def wait_for_astrbot(timeout=180):
    """就绪判定（P1-6）：面板健康 + QQ 通道都通才算就绪；5xx/鉴权失败不算。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        try:
            req = urllib.request.Request(f"{ASTRBOT_BASE}/api/v1/im/bots",
                                         headers={"X-API-Key": API_KEY})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
            if isinstance(d, dict) and d.get("status") == "ok":
                if _qq_connected():
                    log("AstrBot + QQ 通道已就绪")
                    return True
                last = "QQ(6199) 未连接"
                log("AstrBot 已就绪，但 QQ 还没连上，继续等")
            else:
                last = f"面板返回异常: {str(d)[:80]}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            log(f"就绪探测 HTTP {e.code}（不认作就绪）")
        except Exception as e:  # noqa: BLE001
            last = str(e)[:80]
        time.sleep(5)
    log(f"等待就绪超时（{timeout}s，最后：{last}），仍继续启动")
    return False


# ---------- 发送 ----------
def upload_file(path):
    """上传文件到 AstrBot，返回 attachment_id（按扩展名设置正确的媒体类型）。"""
    import uuid as _uuid
    boundary = f"----DemoBotBoundary{_uuid.uuid4().hex}"
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    mime = {
        ".mp3": "audio/mpeg", ".wav": "audio/x-wav", ".silk": "audio/silk",
        ".amr": "audio/amr", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += Path(path).read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{ASTRBOT_BASE}/api/v1/file",
        data=body,
        headers={"X-API-Key": API_KEY,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    data = d.get("data") or {}
    aid = data.get("attachment_id") or data.get("id")
    if not aid:
        log(f"上传响应异常: {json.dumps(d, ensure_ascii=False)[:200]}")
        raise RuntimeError("no attachment_id in upload response")
    return aid


def get_task_status(token):
    """按 task_id 只查状态，不重发。返回 sent/failed/unknown/sending/pending/dropped/..."""
    try:
        url = (f"{ASTRBOT_BASE}/api/v1/im/message/status?task_id="
               + urllib.parse.quote(str(token)))
        req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return ((d.get("data") or {}).get("state") or "").strip() or "unknown"
    except Exception as e:  # noqa: BLE001
        log(f"状态查询失败 task={str(token)[:8]}: {str(e)[:80]}")
        return "unknown"


def send_parts(parts, retries=3, client_token=None, managed_history=False,
               history_text=None):
    """发一条消息（P0-1）：带**稳定的 client_token**，重试复用同一个任务。

    返回 (ok, state)。state = sent / failed / unknown / skipped /
    in_progress / dropped。只有 sent 算成功；unknown/in_progress 绝不重发。
    """
    token = (client_token or uuid.uuid4().hex).strip()
    if not _qq_connected():
        log("QQ 未连接，跳过发送（不建 unknown 任务）")
        return False, "skipped"
    body = {"umo": UMO, "message": parts, "client_token": token}
    if managed_history:
        body["managed_history"] = True
        body["history_text"] = history_text or ""
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    for i in range(retries):
        try:
            r = http_json(ASTRBOT_API, body, headers)
        except urllib.error.HTTPError as e:
            # 服务端明确拒绝（多半是参数/上传问题），请求没发出去，可以重试
            log(f"发送失败({i + 1}): HTTP {e.code} {e.read().decode('utf-8', 'replace')[:120]}")
            time.sleep(8)
            continue
        except Exception as e:  # noqa: BLE001
            # 超时/断连：结果未知，绝不重发
            log(f"发送结果未知({i + 1}): {e} → 不重发")
            _log_unknown(token, parts, f"{type(e).__name__}: {e}")
            return False, "unknown"

        data = r.get("data") or {}
        # 缺失状态不能默认 sent（P0-A）
        state = (data.get("state") or "").strip() or (
            "failed" if r.get("status") == "error" else "unknown")
        err_text = str(data.get("error") or r.get("message") or "")
        # 服务端可能把 ActionFailed 兜底成 unknown；明确 retcode 仍是可重试失败
        if state == "unknown" and _definite_failure(err_text):
            # 服务端把 ActionFailed 兜底成 unknown 时，旧 task 不会再发；
            # 用新 token 建一条新任务，避免“明确没发出去却永久丢掉”。
            log(f"状态 unknown 但错误明确可重试，换新 task 重发: {err_text[:100]}")
            token = uuid.uuid4().hex
            body["client_token"] = token
            # API 不可用通常要等 QQ 重连，多给一点时间
            time.sleep(30 if "ApiNotAvailable" in err_text else 8)
            continue
        task = (data.get("task_id") or token)[:8]
        if state == "unknown":
            log(f"发送结果未知 task={task}（平台可能已收到）→ 不重发")
            _log_unknown(token, parts, str(data.get("error") or "unknown"))
            return False, "unknown"
        if state == "skipped":
            log(f"被门禁跳过 task={task}")
            return False, "skipped"
        if state == "dropped":
            log(f"任务已被人工作废 task={task} → 不再发")
            return False, "dropped"
        if state in ("sending", "pending"):
            # 处理中：只查询，不换 token 重发；最多等 3 次
            for _ in range(3):
                time.sleep(2)
                q = get_task_status(token)
                if q == "sent":
                    log(f"状态查询确认已发送 task={task}")
                    return True, "sent"
                if q in ("failed", "unknown", "dropped"):
                    state = q
                    break
            if state == "failed":
                log(f"发送失败({i + 1}) task={task} → 重试")
                time.sleep(8)
                continue
            log(f"仍在处理中 task={task} state={state} → 不重发")
            return False, "in_progress"
        if state == "failed":
            log(f"发送失败({i + 1}) task={task}: {str(data.get('error') or r.get('message'))[:120]}")
            time.sleep(8)
            continue
        if state != "sent":
            log(f"未知状态 task={task} state={state} → 按未确认处理，不重发")
            return False, state
        log(f"发送成功 task={task} state={state}")
        return True, "sent"

    _log_unknown(token, parts, "重试耗尽（failed）")
    return False, "failed"


def _log_unknown(token, parts, reason):
    try:
        # S10：只记段类型，不把私聊正文写进运维日志。
        kinds = [str(p.get("type") or "?") for p in parts if isinstance(p, dict)]
        line = (f"[{datetime.now():%Y-%m-%d %H:%M:%S}] task={token} reason={reason[:160]} "
                f"parts={json.dumps(kinds, ensure_ascii=False)}")
        import os as _os, tempfile as _tf
        _up = _os.environ.get("DEMO_UNKNOWN_LOG")
        if not _up and _os.environ.get("DEMO_TEST") == "1":
            _up = str(_tf.gettempdir() / "demo_unknown_test.log")
        _upath = _up or str(ROOT_DIR / "ops" / "send_unknown.log")
        with open(_upath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_text(text, **kw):
    return send_parts([{"type": "plain", "text": text}], **kw)


def send_voice(path, **kw):
    if not _qq_connected():
        log("QQ 未连接，语音跳过（不建 unknown 任务）")
        return False, "skipped"
    try:
        aid = upload_file(path)
        return send_parts([{"type": "record", "attachment_id": aid,
                            "filename": os.path.basename(path)}], **kw)
    except Exception as e:  # noqa: BLE001
        log(f"语音发送失败: {e}")
        return False, "failed"


VIDEO_MAX_MB = 25


def _video_plan(size_bytes: int) -> str:
    """视频太大就发链接（QQ 文件上传有配额，别硬发）。"""
    return "send" if size_bytes <= VIDEO_MAX_MB * 1024 * 1024 else "link"


def share_video(path, caption="", **kw):
    """分享一段视频：小就直发，大就发文字链接。返回 (ok, state)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        log(f"视频读不到: {e}")
        return False, "failed"
    if _video_plan(size) == "link":
        msg = (caption + " " if caption else "") + f"视频有点大，在这儿看：{path}"
        return send_text(msg, **kw)
    try:
        aid = upload_file(path)
        parts = [{"type": "video", "attachment_id": aid,
                  "filename": os.path.basename(path)}]
        if caption:
            parts.insert(0, {"type": "plain", "text": caption})
        return send_parts(parts, **kw)
    except Exception as e:  # noqa: BLE001
        log(f"视频发送失败: {e}")
        return False, "failed"


def send_image(path, **kw):
    try:
        aid = upload_file(path)
        return send_parts([{"type": "image", "attachment_id": aid,
                            "filename": os.path.basename(path)}], **kw)
    except Exception as e:  # noqa: BLE001
        log(f"图片发送失败: {e}")
        return False, "failed"


# ---------- 语音合成 ----------
def _speech_text(text):
    """把打字稿变成 TTS 实际要念的稿子；返回的文本要落历史。

    与 minimax_tts_api_source._preprocess_text 保持同一套规则（T01）。
    """
    text = re.sub(r"哈{5,}", "哈哈哈", text)
    text = re.sub(r"呵{3,}", "呵呵", text)
    text = re.sub(r"嘻{3,}", "嘻嘻", text)
    text = text.replace("（", "").replace("）", "")
    # Q6：TTS 旁路也不能带省略号
    text = re.sub(r"[…]+|\.{3,}", " ", text)
    text = re.sub(r"\s+", "，", text).strip("， ")
    text = re.sub(r"([？！。])，", r"\1", text)
    text = re.sub(r"^(在吗|在不在)(?![？?])", r"\1？", text)
    if text and not re.search(r"[。！？!?…]$", text):
        text = text + "。"
    return text


def synth_voice_ex(text):
    """返回 (音频路径, 实际念出的稿子)。"""
    spoken = _speech_text(text)
    key = Path(MINIMAX_KEY_FILE).read_text(encoding="utf-8-sig").strip()
    payload = {
        "model": "speech-2.8-hd",
        "text": spoken,
        "stream": False,
        "voice_setting": {"voice_id": "DEMO_VOICE", "speed": 0.95, "vol": 1.0, "pitch": 1, "emotion": "calm"},
        "audio_setting": {"sample_rate": 32000, "format": "mp3", "channel": 1},
    }
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/t2a_v2",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(
            urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    base = d.get("base_resp") or {}
    code = base.get("status_code")
    if code not in (0, None):
        raise RuntimeError(
            f"MiniMax TTS 业务错误 {code}: {base.get('status_msg') or ''}"
        )
    data = d.get("data") or {}
    # 非流式响应正常 status=2；缺失时兼容旧返回，但有 audio 才继续。
    if data.get("status") not in (2, None):
        raise RuntimeError(f"MiniMax TTS 未完成: status={data.get('status')!r}")
    raw = bytes.fromhex(data.get("audio") or "")
    if not raw:
        raise RuntimeError("MiniMax TTS 返回空音频")
    path = rf"{SCREEN_DIR}\voice_{int(time.time())}.mp3"
    Path(path).write_bytes(raw)
    return path, spoken


def synth_voice(text):
    return synth_voice_ex(text)[0]


# ---------- 生成 ----------
def gen_proactive(instruction="", max_chars=40, save=True):
    """同源生成（P0-4）：同一份配置 + 真实历史 + 一次性 instruction，只写 assistant。"""
    body = {"umo": UMO, "instruction": instruction, "max_chars": max_chars,
            "save": bool(save), "max_history": 80}
    r = http_json(PROACTIVE_API, body,
                  {"X-API-Key": API_KEY, "Content-Type": "application/json"}, timeout=90)
    if not isinstance(r, dict) or r.get("status") == "error":
        raise RuntimeError(f"proactive API: {str(r)[:160]}")
    data = r.get("data") or {}
    text = (data.get("text") or "").strip()
    if not text:
        raise RuntimeError("proactive API 返回空文本")
    log(f"同源生成: {text} | {data.get('actions') or ''} "
        f"{data.get('model')} {data.get('elapsed_ms')}ms")
    return text


def style_core(limit=1800):
    """T7：给兜底路径用的「风格核心」。

    以前这里取的是前 1500 字——那是身份 + 开头几条规则，**风格段全丢了**。
    现在按标题把「怎么说话」「不会这样」「标点」三段抽出来，拼成 1.5~1.8k。
    """
    try:
        full = Path(PERSONA_FILE).read_text(encoding="utf-8")
    except Exception:
        return "你是Bot。"
    heads = ["【机器人怎么说话", "【标点与断句", "【机器人不会这样说话"]
    parts = []
    for h in heads:
        i = full.find(h)
        if i < 0:
            continue
        j = full.find("\n【", i + 1)
        parts.append(full[i:j if j > 0 else len(full)].strip())
    core = "\n\n".join(parts).strip()
    if not core:
        return full[:1500]
    return core[:limit]


def gen_topic_fallback():
    """P3-4：回退不再自行生成日常；无同源状态就返回空、跳过可选主动消息。"""
    return ""
    try:
        persona = style_core()
    except Exception:
        persona = "你是Bot。"
    p = random.choice([
        "跟我说句日常吧", "分享一下你现在在干嘛", "主动跟我说句话",
        "有点关心了没 说句话", "随便说点啥", "今天过得怎么样 说一句",
    ])
    hour = datetime.now().hour
    p += ("（现在是深夜）" if 23 <= hour or hour < 6 else
          "（现在是上午）" if 6 <= hour < 12 else
          "（现在是下午）" if 12 <= hour < 18 else "（现在是晚上）")
    try:
        payload = {
            "model": "demo",
            "messages": [{"role": "system", "content": persona},
                         {"role": "user", "content": p}],
            "stream": False,
        }
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        msg = (d.get("message") or {}).get("content", "").strip().strip('"')
        msg = msg.replace("\n", " ").replace("\r", " ")
        msg = re.sub(r"[（(]\s*[）)]", "", msg)
        msg = re.sub(r"（([^（）]{1,3})$", r"\1（", msg).strip(" （）")
        if 2 <= len(msg) <= 30:
            log(f"话题(Ollama): {msg}")
            return msg
    except Exception as e:  # noqa: BLE001
        log(f"Ollama 失败: {str(e)[:80]}")

    prompt = (
        persona
        + "\n\n现在请以Bot的身份，主动给用户发一条消息，分享你此刻的日常/心情/游戏/练习"
          "（10~25字），符合你的说话风格：短句、可带半括号、可有哈哈哈，不用任何 emoji。"
          "只输出这一条消息本身。"
    )
    for _ in range(2):
        try:
            r = http_json(
                DEEPSEEK_API,
                {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                 "max_tokens": 80, "temperature": 0.9, "stream": False},
                {"Authorization": f"Bearer {DEEPSEEK_KEY}",
                 "Content-Type": "application/json"},
            )
            msg = r["choices"][0]["message"]["content"].strip().strip('"')
            msg = msg.replace("\n", " ").replace("\r", " ")
            if 4 <= len(msg) <= 60:
                return msg
        except Exception as e:  # noqa: BLE001
            log(f"话题生成失败: {e}")
    return random.choice(DAILY_TEMPLATES)


def _yesterday_str():
    return datetime.fromordinal(datetime.now().toordinal() - 1).strftime("%Y-%m-%d")


def _pick_angle(state):
    """当天角度不重复；10 个都用完了才允许重置。"""
    today = datetime.now().strftime("%Y-%m-%d")
    ua = state.get("used_angles") or {}
    keep = {today, _yesterday_str()}
    ua = {k: v for k, v in ua.items() if k in keep}
    used = set(ua.get(today) or [])
    idxs = [i for i in range(len(ACTIVITY_ANGLES)) if i not in used]
    if not idxs:
        used = set()
        idxs = list(range(len(ACTIVITY_ANGLES)))
    i = random.choice(idxs)
    used.add(i)
    ua[today] = sorted(used)
    state["used_angles"] = ua
    return ACTIVITY_ANGLES[i]


ANCHOR_SKIP_PREFIX = (
    "你好", "在吗", "晚安", "早安", "晚上好", "中午好", "下午好",
    "吃了吗", "睡了", "哈哈", "嗯", "好", "在干嘛", "我关心你", "我认可你",
)
ANCHOR_HINTS = (
    "音乐", "乐器", "作业", "考试", "游戏", "日常",
    "原神", "视频", "B站", "吃", "面", "火锅", "冰淇淋", "感冒", "药",
    "睡", "东京", "演出", "练习", "课",
)


def _recent_user_messages(limit=200):
    """从 conversation 里取最近用户发的话，带时间，过滤问候/表达/问句。"""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            "select content from conversations where user_id=?", (UMO,)).fetchone()
        con.close()
    except Exception as e:  # noqa: BLE001
        log(f"读最近对话失败: {str(e)[:60]}")
        return []
    if not row or not row[0]:
        return []
    try:
        msgs = json.loads(row[0])
    except Exception:
        return []
    out = []
    for m in msgs[-limit:]:
        if m.get("role") != "user":
            continue
        parts = m.get("content") or []
        raw = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        mm = re.search(r"Current datetime:\s*([0-9-]+ [0-9:]+)", raw)
        dt = mm.group(1) if mm else ""
        text = re.sub(r"<system_reminder>.*?</system_reminder>", "", raw, flags=re.S).strip()
        if len(text) < 5 or len(text) > 40:
            continue
        if any(text.startswith(x) for x in ANCHOR_SKIP_PREFIX):
            continue
        if "我认可你" in text or "关心你" in text:
            continue
        if any(q in text for q in ("什么", "干嘛", "怎么", "为什么", "是不是",
                                   "哪", "几点", "多少", "吗")):
            continue
        if text.endswith(("呢", "嘛", "吧")):
            continue
        if any(x in text for x in ("说过", "聊过", "问过", "去吧", "好好休息", "摸摸")):
            continue
        score = sum(1 for h in ANCHOR_HINTS if h in text)
        if score == 0:
            continue
        out.append({"text": text, "dt": dt, "score": score})
    return out


def _anchor_age(dt):
    try:
        d = datetime.strptime(dt, "%Y-%m-%d %H:%M")
        days = (datetime.now() - d).days
        if days <= 0:
            return "今天"
        if days == 1:
            return "昨天"
        return f"{days}天前"
    except Exception:  # noqa: BLE001
        return "最近"


def _pick_anchor(state):
    """挑一条用户最近说过的具体事，做主动消息的由头；7 天内不重复。"""
    cands = _recent_user_messages()
    if not cands:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    ua = state.get("used_anchors") or {}
    keep = {(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(7)}
    ua = {k: v for k, v in ua.items() if k in keep}
    used = {str(x) for v in ua.values() for x in (v or [])}
    cands = [c for c in cands if c["text"] not in used][-20:]
    if not cands:
        return None
    weights = [1 + c["score"] for c in cands]
    c = random.choices(cands, weights=weights, k=1)[0]
    ua.setdefault(today, []).append(c["text"])
    state["used_anchors"] = ua
    return {"text": c["text"], "age": _anchor_age(c["dt"])}


def _recent_texts(state, hours=6):
    now = time.time()
    return [x for x in (state.get("recent_texts") or [])
            if isinstance(x, dict) and now - float(x.get("t") or 0) < hours * 3600]


def _is_repeat(state, text):
    import difflib
    if not text:
        return False
    for x in _recent_texts(state):
        old = (x.get("s") or "").strip()
        if not old:
            continue
        if old == text or difflib.SequenceMatcher(None, old, text).ratio() >= 0.85:
            return True
    return False


def _remember_text(state, text):
    rt = _recent_texts(state, hours=24)
    rt.append({"t": int(time.time()), "s": text})
    state["recent_texts"] = rt[-10:]


def gen_topic(state=None):
    """主动话题：当天角度不重复；6 小时内近似就换角度重生成一次。"""
    if state is None:
        state = load_state()
    _life_line = _life_proactive_line()
    msg = ""
    for attempt in range(2):
        anchor = _pick_anchor(state) if random.random() < 0.65 else None
        if anchor:
            instruction = (
                "（这是你自己想说话的時刻，不是用户找你。你现在主动给用户发一条消息。）\n"
                f"接着用户{anchor['age']}说过的『{anchor['text']}』说一句或问一句。\n"
                "不要用「晚上好」「在吗」「吃了吗」开头，不要复述原话；8~20 字；"
                "极短、用空格断句、句尾最多一个半括号、不用句号不用 emoji；"
                "不提「系统」「提示」「指令」。只输出这一句，不要引号。"
            )
            log(f"主动锚点: {anchor['age']} {anchor['text'][:24]}")
        else:
            angle = _pick_angle(state)
            instruction = (
                "（这是你自己想说话的時刻，不是用户找你。你现在主动给用户发一条消息。）\n"
                f"这次的角度：{angle}。\n"
                "不要用「晚上好」「在吗」「吃了吗」开头；"
                "只写你要发出去的那句话，8~25 字；按你平时的说法——极短、用空格断句、"
                "句尾最多一个半括号、不用句号不用 emoji；不提「系统」「提示」「指令」；"
                "不要复述用户刚说过的话。只输出这一句，不要引号。"
            )
        try:
            msg = gen_proactive((_life_line + "\n" + instruction) if _life_line else instruction)
        except Exception as e:  # noqa: BLE001
            log(f"同源生成失败，跳过本轮（不回退本地日常）: {str(e)[:120]}")
            return ""
        if not msg:
            log("同源生成返回空，跳过本轮")
            return ""
        if not _is_repeat(state, msg):
            break
        log(f"与最近 6 小时重复，换角度重生成（{attempt + 1}/2）: {msg[:24]}")
    else:
        # P0：两次都重复就放弃本轮，不能把最后一条重复文本当成新话题发出去。
        log("两次生成均与最近 6 小时重复，放弃本轮")
        return ""
    _remember_text(state, msg)
    save_state(state)
    return msg


def gen_voice_text(state=None):
    """语音口语版：同源；有锚点就接着用户说过的事，失败返回空。"""
    try:
        base = VOICE_INSTRUCTION
        if state is not None and random.random() < 0.65:
            anchor = _pick_anchor(state)
            if anchor:
                base = (f"（接着用户{anchor['age']}说过的『{anchor['text']}』说一句。）\n"
                        + base)
                log(f"语音锚点: {anchor['age']} {anchor['text'][:24]}")
        _life_line = _life_proactive_line()
        if _life_line:
            base = _life_line + "\n" + base
        return gen_proactive(base, max_chars=70)
    except Exception as e:  # noqa: BLE001
        log(f"语音文本同源生成失败，跳过本轮: {str(e)[:100]}")
        return ""


# ---------- 打断保护（P0-2） ----------
def user_recently_active(minutes=10):
    """用户最近是否在聊天（避免主动消息打断）。

    updated_at 现在有两种写法：AstrBot 的会话更新是本地 naive 时间，
    send_ledger.commit_history 写的是 UTC。不能一律按 UTC 解释，否则
    本地 17:13 会被当成未来 8 小时，主动消息会被误判“刚聊过”而一直跳过。
    """
    import sqlite3

    try:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = db.execute(
            "SELECT updated_at FROM conversations WHERE user_id=?", (UMO,)
        ).fetchone()
        db.close()
        if not row or not row[0]:
            return False
        ts = datetime.fromisoformat(str(row[0]))
        now_utc = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            as_utc = ts.replace(tzinfo=timezone.utc)
            if as_utc > now_utc + timedelta(minutes=5):
                # 未来超过 5 分钟：说明这行其实是本地时间
                ts = ts.astimezone().astimezone(timezone.utc)
            else:
                ts = as_utc
        return (now_utc - ts).total_seconds() < minutes * 60
    except Exception as e:  # noqa: BLE001
        log(f"活跃度检查失败（按未活跃处理）: {e}")
        return False


# ---------- B 站分享 ----------
def _log_external_source(rec):
    """外部见闻的来源账：链接、抓取时间、实际读取范围。"""
    try:
        path = EXTERNAL_LOG
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log(f"外部见闻来源记录失败: {str(e)[:80]}")


def _last_external_source(max_age_hours=168):
    """最近一条可回访的外部来源；太旧/不是外部的返回 None。"""
    try:
        p = Path(EXTERNAL_LOG)
        if not p.exists():
            return None
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if not rec.get("url") or not rec.get("title"):
                continue
            if rec.get("stale"):
                continue
            try:
                ts = datetime.fromisoformat(rec.get("fetched_at") or "")
                age = (datetime.now() - ts).total_seconds()
            except Exception:  # noqa: BLE001
                age = 0
            if age <= max_age_hours * 3600:
                return rec
    except Exception as e:  # noqa: BLE001
        log(f"读外部来源失败: {str(e)[:80]}")
    return None


def _share_stale_bili(reason="412"):
    """B站抓不到时，退而分享一条**有来源的旧素材**；不生成假见闻。"""
    rec = _last_external_source()
    if not rec:
        return False
    text = f"之前刷到一个 一直没给你看（\n{rec['url']}"
    ok, _st = send_parts([{"type": "plain", "text": text}],
                         managed_history=True, history_text=text)
    if not ok:
        return False
    _log_external_source({
        **rec,
        "shared": True,
        "stale": True,
        "stale_reason": reason,
        "stale_shared_at": datetime.now().isoformat(timespec="seconds"),
    })
    log(f"B站分享：{reason}，改用旧素材 {rec.get('bvid') or rec['url']}")
    return True


def _bili_fallback(blob):
    """同源生成失败时只给短句，不编“看完了”的细节。"""
    if any(k in blob for k in ("攻略", "教程", "教学")):
        return "这个攻略看着挺细的 先码住（"
    if any(k in blob for k in ("实况", "搞笑", "整活")):
        return "这个实况看着挺乐的 等会看（"
    if any(k in blob for k in ("演奏", "翻唱", "音乐", "乐器")):
        return "这个演奏听着应该挺舒服的（"
    if any(k in blob for k in ("建筑", "生存", "我的世界", "MC")):
        return "这个建得也太费时间了（"
    return "这个看着有点意思 我先码住（"


def _bili_meta_only(text):
    """只评价时长/标题/UP，没有落到内容点，就不算合格文案。"""
    meta = ("时长", "分钟", "这么长", "太长了", "好长", "多长", "标题", "UP", "分区")
    content = ("好玩", "好笑", "笑死", "梗", "剧情", "操作", "画面", "音乐", "演奏",
               "建筑", "树屋", "苦力怕", "攻略", "实况", "整活", "翻车", "惨",
               "有意思", "可爱", "帅", "离谱")
    return any(k in text for k in meta) and not any(k in text for k in content)


def _bili_clean(text, title="", limit=28):
    text = re.sub(r"https?://\S+", "", text or "")
    text = text.replace("\n", " ").replace("\r", " ").strip()
    if title and title in text:
        text = text.replace(title, "").strip()
    text = re.sub(r"\s+", " ", text).strip(" ，,。.（）()")
    if len(text) > limit:
        text = text[:limit].rstrip(" ，,。.")
    return text


def _bili_bracket(text, bracket):
    text = (text or "").strip()
    if not text:
        return ""
    if not text.endswith(("（", "）")):
        text += bracket
    return text


def _bili_3g(text):
    t = re.sub(r"\s+", "", text or "")
    if len(t) < 3:
        return {t} if t else set()
    return {t[i:i + 3] for i in range(len(t) - 2)}


def _bili_lines(v):
    """两句：一句“哪里好玩”，一句感受；分别发，自带半括号。"""
    title = (v.get("title") or "").replace('<em class="keyword">', "").replace("</em>", "")
    author = v.get("author") or (v.get("owner") or {}).get("name") or ""
    tname = v.get("tname") or ""
    desc = v.get("description") or v.get("desc") or ""
    blob = f"{title} {desc}"

    fun_prompt = (
        "（这是你自己刷到 B 站视频后，想主动分享给用户，不是用户找你。）\n"
        f"视频信息：标题「{title}」；UP「{author}」；分区「{tname}」；"
        f"简介「{desc[:200]}」。\n"
        "只写一句：你觉得这个视频哪里好玩/哪里想看，10~24 字。"
        "必须点一个具体内容点（玩法/剧情/操作/画面/打斗/音乐/建筑/笑点/翻车点）；"
        "不要写感受，不要评价时长，不要只说标题/UP/分区，不要复述标题，"
        "不要提系统、推荐、算法、链接。短句、空格断句、不用句号/emoji，"
        "句尾带一个半括号（或）。只输出这一句。"
    )
    fun = ""
    try:
        fun = _bili_clean(gen_proactive(fun_prompt, max_chars=40, save=False), title, 28)
        if len(fun) < 6 or _bili_meta_only(fun):
            log(f"B站“哪里好玩”不合格，丢弃: {fun[:40]}")
            fun = ""
    except Exception as e:  # noqa: BLE001
        log(f"B站“哪里好玩”生成失败: {str(e)[:80]}")
    if not fun:
        fun = _bili_fallback(blob)

    feel_prompt = (
        f"你刚说：{fun}\n"
        "只写一句你现在的感受/要不要看，8~18 字。"
        "不要重复上一句里的名词、画面或剧情，不要提标题、UP、链接、系统、推荐、算法；"
        "直接写你的反应，比如“有点想看了”“等会去瞄一眼”“感觉会挺有意思”。"
        "短句、空格断句、不用句号/emoji，句尾带一个半括号（或）。只输出这一句。"
    )
    feel = ""
    try:
        feel = _bili_clean(gen_proactive(feel_prompt, max_chars=40, save=False), "", 20)
        if (len(feel) < 4 or _bili_meta_only(feel) or feel == fun
                or (_bili_3g(fun) & _bili_3g(feel))):
            feel = ""
    except Exception as e:  # noqa: BLE001
        log(f"B站感受句生成失败: {str(e)[:80]}")
    if not feel:
        feel = random.choice(("有点想看了）", "等会去瞄一眼）", "感觉会挺有意思）"))

    return _bili_bracket(fun, "（"), _bili_bracket(feel, "）")


def _bili_comment(v):
    """兼容旧调用：只取“哪里好玩”那一句。"""
    return _bili_lines(v)[0]


def _bili_text(url, v=None):
    """兼容旧调用：两句 + 链接。"""
    fun, feel = _bili_lines(v or {})
    return f"{fun}\n{feel}\n{url}"


def _bili_grams(text):
    t = re.sub(r"[\s\W_]+", "", (text or "").lower())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _bili_fetch_cid(v):
    cid = v.get("cid")
    if not cid:
        pages = v.get("pages") or []
        if pages:
            cid = pages[0].get("cid")
    if cid:
        return int(cid)
    bvid = v.get("bvid")
    if not bvid:
        return None
    try:
        r = http_json(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            headers=BILI_HEADERS, timeout=20)
        return (r.get("data") or {}).get("cid")
    except Exception as e:  # noqa: BLE001
        log(f"B站取 cid 失败: {str(e)[:80]}")
        return None


def _bili_danmaku(cid):
    """取弹幕 (time, text)；B 站 XML 可能是 raw deflate。"""
    try:
        req = urllib.request.Request(
            f"https://comment.bilibili.com/{cid}.xml",
            headers={**BILI_HEADERS, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(2_000_000)
        if not raw.lstrip().startswith(b"<"):
            try:
                raw = zlib.decompress(raw, -15)
            except zlib.error:
                raw = zlib.decompress(raw, 31)
        text = raw.decode("utf-8", "ignore")
        rows = []
        for p, txt in re.findall(r'<d p="([^"]+)"[^>]*>(.*?)</d>', text, re.S):
            try:
                t = float(p.split(",", 1)[0])
            except ValueError:
                continue
            txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
            if txt:
                rows.append((t, txt))
        return rows[:8000]
    except Exception as e:  # noqa: BLE001
        log(f"B站弹幕失败: {str(e)[:80]}")
        return []


def _bili_match_danmaku(fun, rows):
    """用机器人说的话去弹幕里找最接近的一刻；不够像就返回 None，回退封面。"""
    fg = _bili_grams(fun)
    if len(fg) < 2:
        return None
    best = (0.0, None, "")
    for t, txt in rows:
        dg = _bili_grams(txt)
        if not dg:
            continue
        inter = len(fg & dg)
        if inter < 2:
            continue
        score = inter / ((len(fg) * len(dg)) ** 0.5)
        if score > best[0]:
            best = (score, t, txt)
    if best[1] is not None and best[0] >= 0.45:
        return best
    return None


def _bili_videoshot_frame(cid, bvid, at):
    """从 videoshot 雪碧图裁出 at 秒附近的一帧；失败返回 None。"""
    if Image is None:
        return None
    try:
        r = http_json(
            "https://api.bilibili.com/x/player/videoshot"
            f"?bvid={bvid}&cid={cid}&index=1",
            headers=BILI_HEADERS, timeout=20)
        d = r.get("data") or {}
        images = d.get("image") or []
        idx = [float(x) for x in (d.get("index") or [])]
        if not images or not idx:
            return None
        pos = min(range(len(idx)), key=lambda i: abs(idx[i] - at))
        xlen = int(d.get("img_x_len") or 10)
        ylen = int(d.get("img_y_len") or 10)
        xsize = int(d.get("img_x_size") or 480)
        ysize = int(d.get("img_y_size") or 270)
        if pos >= xlen * ylen:
            return None
        url = images[0]
        if url.startswith("//"):
            url = "https:" + url
        req = urllib.request.Request(url, headers=BILI_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r2:
            raw = r2.read(3_000_000)
        sheet = Image.open(io.BytesIO(raw))
        col, row = pos % xlen, pos // xlen
        box = (col * xsize, row * ysize, (col + 1) * xsize, (row + 1) * ysize)
        if box[2] > sheet.width or box[3] > sheet.height:
            return None
        frame = sheet.crop(box).convert("RGB")
        path = os.path.join(
            SCREEN_DIR, f"Bot帧_{datetime.now():%m%d_%H%M%S}.jpg")
        frame.save(path, quality=92)
        log(f"B站帧已存: {path} t={idx[pos]:.0f}s")
        return path, idx[pos]
    except Exception as e:  # noqa: BLE001
        log(f"B站抽帧失败: {str(e)[:80]}")
        return None


def _bili_frame(v, fun):
    """机器人说的话 -> 弹幕相似时刻 -> 视频帧；没有把握返回 None。"""
    if Image is None:
        return None
    bvid = v.get("bvid")
    cid = _bili_fetch_cid(v)
    if not bvid or not cid:
        return None
    rows = _bili_danmaku(cid)
    hit = _bili_match_danmaku(fun, rows)
    if not hit:
        log("B站抽帧：弹幕里没有足够像的时刻，回退封面")
        return None
    score, at, txt = hit
    got = _bili_videoshot_frame(cid, bvid, at)
    if not got:
        return None
    path, actual = got
    log(f"B站抽帧对齐: {score:.2f} «{txt[:20]}» @{actual:.0f}s")
    return path, actual, txt


def _bili_cover(v):
    """优先取视频封面：和标题/内容同源，不会截到无关页面。"""
    pic = str(v.get("pic") or "").strip()
    if not pic:
        return None
    if pic.startswith("//"):
        pic = "https:" + pic
    path = os.path.join(
        SCREEN_DIR, f"Bot封面_{datetime.now():%m%d_%H%M%S}.jpg")
    try:
        req = urllib.request.Request(
            pic, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            })
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(3_000_000)
        if len(data) < 5000:
            return None
        if not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
            return None
        Path(path).write_bytes(data)
        log(f"B站封面已存: {path}")
        return path
    except Exception as e:  # noqa: BLE001
        log(f"B站封面下载失败: {str(e)[:80]}")
        return None


def _bili_page_shot(url):
    """封面拿不到时才回退页面截图；单独 25s 超时，不阻塞文字。"""
    shot = os.path.join(
        SCREEN_DIR, f"Bot截图_{datetime.now():%m%d_%H%M%S}.png")
    edge = (shutil.which("msedge")
            or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    try:
        subprocess.run(
            [edge, "--headless", "--disable-gpu", f"--screenshot={shot}",
             "--window-size=1280,800", "--virtual-time-budget=10000", url],
            capture_output=True, timeout=25,
        )
    except Exception as e:  # noqa: BLE001
        log(f"截图调用失败: {e}")
    if os.path.exists(shot) and os.path.getsize(shot) > 5000:
        return shot
    return None


def bili_share():
    kw = random.choice(BILI_KEYWORDS)
    try:
        vids = []
        try:
            r = http_json(BILI_API.format(urllib.parse.quote(kw)),
                          headers=BILI_HEADERS)
            vids = (r.get("data") or {}).get("result", []) or []
        except urllib.error.HTTPError as e:
            # 搜索接口对中文关键词长期 412；回落全站热门，至少还能真分享。
            if e.code != 412:
                raise
            log("B站搜索 412，改取全站热门")
        if not vids:
            r = http_json(BILI_POPULAR_API, headers=BILI_HEADERS)
            vids = (r.get("data") or {}).get("list", []) or []
        if not vids:
            return False
        v = random.choice(vids[:8])
        bvid = v.get("bvid")
        title = (v.get("title", "")
                 .replace('<em class="keyword">', "").replace("</em>", ""))
        url = f"https://www.bilibili.com/video/{bvid}"
        fun, feel = _bili_lines(v)
        text1 = fun
        text2 = f"{feel}\n{url}"
        # 分段发：先“哪里好玩”，再“感受 + 链接”；每段都单独进历史。
        ok, _st = send_parts([{"type": "plain", "text": text1}],
                             managed_history=True, history_text=text1)
        if not ok:
            log("B站分享：第一段发送失败，本次放弃")
            return False
        ok2, _st2 = send_parts([{"type": "plain", "text": text2}],
                               managed_history=True, history_text=text2)
        if not ok2:
            log("B站分享：第二段发送失败，第一段已出；改发纯链接")
            ok3, _st3 = send_parts([{"type": "plain", "text": url}],
                                   managed_history=True, history_text=url)
            if not ok3:
                log("B站分享：纯链接也没发出去")
            return True
        # 没有视觉校验时默认不发帧，避免弹幕关键词撞错画面；需要时显式开。
        frame = (_bili_frame(v, fun)
                 if os.environ.get("DEMO_BILI_FRAME", "0").strip() == "1"
                 else None)
        image_path = frame[0] if frame else None
        image_kind = "frame" if frame else None
        if not image_path:
            image_path = _bili_cover(v)
            image_kind = "cover" if image_path else None
        if not image_path and os.environ.get("DEMO_BILI_PAGE_SHOT", "0").strip() == "1":
            image_path = _bili_page_shot(url)
            image_kind = "screenshot" if image_path else None
        rec = {
            "kind": "bilibili",
            "bvid": bvid,
            "url": url,
            "title": title,
            "segments": [text1, text2],
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "read_scope": "title",   # 搜索接口只给了标题，没有简介/正文
            "shared": True,
            "image_kind": image_kind,
            "screenshot": False,
            "screenshot_url": url if image_path else None,
            "screenshot_path": image_path,
        }
        if frame:
            rec["frame_at"] = frame[1]
            rec["frame_text"] = frame[2]
        if image_path:
            img_ok, _ = send_image(image_path)
            rec["screenshot"] = bool(img_ok)
            if not img_ok:
                log("B站分享：配图发送失败，链接已发出")
            else:
                log(f"B站配图已发（{image_kind}）: {image_path}")
        else:
            log("B站配图失败，仅发文字+链接")
        _log_external_source(rec)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 412:
            if _share_stale_bili("412"):
                return True
            log("B站分享：412（风控/签名），没有可用旧素材，本次不分享")
        else:
            log(f"B站分享 HTTP 失败: {e.code}")
        return False
    except Exception as e:  # noqa: BLE001
        log(f"B站分享失败: {e}")
        return False


# ---------- 主流程 ----------
def _remember_send(state, kind):
    state["last_sent"] = int(time.time())
    state["last_kind"] = kind
    state["unanswered"] = int(state.get("unanswered") or 0) + 1
    _bump_daily(state)
    save_state(state)


def _today_key() -> str:
    return datetime.now().date().isoformat()


def _daily_count(state) -> int:
    daily = state.get("daily") or {}
    if daily.get("date") != _today_key():
        return 0
    try:
        return int(daily.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _bump_daily(state) -> None:
    today = _today_key()
    daily = state.get("daily") or {}
    count = _daily_count(state) if daily.get("date") == today else 0
    state["daily"] = {"date": today, "count": count + 1}


def _voice_quota_ok(state) -> bool:
    """S8：每日 TTS 次数上限，默认 20；0=不限。"""
    try:
        limit = int(os.environ.get("DEMO_DAILY_TTS_LIMIT", "20"))
    except ValueError:
        limit = 20
    if limit <= 0:
        return True
    today = _today_key()
    q = state.get("daily_voice") or {}
    count = int(q.get("count") or 0) if q.get("date") == today else 0
    if count >= limit:
        return False
    state["daily_voice"] = {"date": today, "count": count + 1}
    return True


def _last_user_activity_ts() -> int:
    """最近一次被动回复的时间；用它判断用户在上次主动后有没有回来。"""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT MAX(created_at) FROM yy_send_task"
                " WHERE history_state='external' AND state='sent'").fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return 0
        return int(datetime.fromisoformat(str(row[0])).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _proactive_allowed(state) -> tuple[bool, str]:
    if os.path.exists(PROACTIVE_DISABLED):
        return False, "主动消息已被 Bot管家.disabled 关闭"
    if os.environ.get("DEMO_PROACTIVE_ENABLED", "1").strip().lower() in (
            "0", "false", "off", "no"):
        return False, "主动消息已被 DEMO_PROACTIVE_ENABLED=0 关闭"
    if MAX_PROACTIVE_PER_DAY > 0 and _daily_count(state) >= MAX_PROACTIVE_PER_DAY:
        return False, f"今日主动已达上限 {MAX_PROACTIVE_PER_DAY} 条"
    last_sent = int(state.get("last_sent") or 0)
    last_user = _last_user_activity_ts()
    if last_user and last_sent and last_user > last_sent:
        state["unanswered"] = 0
    if int(state.get("unanswered") or 0) >= 2:
        return False, "连续 2 轮主动未回复，退避到用户下次开口"
    return True, ""


def _life_gate_blocks() -> bool:
    """L5：启用生活层时才拦睡眠静默；影子模式不改变真实发送。"""
    try:
        from astrbot.core.utils import life_state as _ls
        if not _ls.enabled() or _ls.shadow():
            return False
        return not _ls.proactive_allowed()
    except Exception:  # noqa: BLE001
        return False


def _life_proactive_line() -> str:
    """主动消息也要知道机器人自己在干嘛，避免"在睡觉却问在干嘛"。影子只记日志。"""
    try:
        from astrbot.core.utils import life_state as _ls
        if not _ls.enabled():
            return ""
        ctx = _ls.life_context(UMO, purpose="proactive")
        text = ctx.get("text", "") if isinstance(ctx, dict) else str(ctx or "")
        text = (text or "").strip()
        if not text:
            return ""
        if _ls.shadow():
            log(f"[生活层影子] 主动消息将带上：{text[:60]}")
            return ""
        return text
    except Exception as e:  # noqa: BLE001
        log(f"生活层上下文失败: {str(e)[:80]}")
        return ""


def _life_practice_cue():
    """L6：启用生活层且提醒开关打开时，给一次练习 cue；否则 None。"""
    if os.environ.get("DEMO_LIFE_REMINDER", "0").strip() in (
            "0", "", "false", "False", "off", "no"):
        return None
    try:
        from astrbot.core.utils import life_state as _ls
        if not _ls.enabled() or _ls.shadow():
            return None
        st = _ls.advance(UMO)
        if st.get("title") != "练习" or not st.get("event_id"):
            return None
        ev = _ls.event_by_id(st["event_id"])
        if not ev or ev.get("told_at"):
            return None
        if not _ls.reminder_allowed(now=st.get("now")):
            return None
        return st["event_id"], "练习中 晚点找你（"
    except Exception as e:  # noqa: BLE001
        log(f"生活层提醒判断失败: {str(e)[:80]}")
        return None


def act(state=None):
    state = state if state is not None else load_state()
    allowed, reason = _proactive_allowed(state)
    if not allowed:
        log(reason + "，跳过本次主动消息")
        return
    if user_recently_active(10):
        log("用户刚聊过，跳过本次主动消息")
        return
    if _life_gate_blocks():
        log("生活层静默时段，跳过本次主动消息")
        return
    cue = _life_practice_cue()
    if cue:
        eid, cue_text = cue
        ok, send_state = send_text(cue_text, managed_history=True,
                                   history_text=cue_text)
        if ok:
            try:
                from astrbot.core.utils import life_state as _ls
                _ls.mark_event_told(eid)
            except Exception:  # noqa: BLE001
                pass
            _remember_send(state, "text")
        else:
            log(f"生活层练习提醒没发出去（state={send_state}），本轮放弃")
        return
    if random.random() < BILI_CHANCE:
        if bili_share():
            _remember_send(state, "bili")
            return
        log("B站分享没成功，改走普通主动消息")

    r = random.random()
    if r < TEXT_RATIO:
        msg = gen_topic(state)
        if not msg:
            log("没有可信的同源主动内容，跳过本轮")
            return
        ok, st = send_text(msg, managed_history=True)
        if ok:
            _remember_send(state, "text")
        else:
            log(f"主动文字没发出去（state={st}），本轮放弃")
        return

    if r < TEXT_RATIO + VOICE_RATIO:
        if not _voice_quota_ok(state):
            log("今日语音已达上限，本轮跳过")
            return
        vtext = gen_voice_text(state)
        if not vtext:
            log("没有可信的同源语音内容，跳过本轮")
            return
        try:
            vpath, spoken = synth_voice_ex(vtext)
            # A05：历史记“实际念出来的稿子”，不是打字稿
            ok, st = send_voice(vpath, managed_history=True,
                                history_text=spoken)
        except Exception as e:  # noqa: BLE001
            log(f"语音链路异常: {e}")
            ok, st = False, "failed"
        if ok:
            _remember_send(state, "voice")
            return
        log(f"语音没发出去（state={st}）")
        if st in ("unknown", "in_progress", "dropped", "skipped"):
            log(f"状态={st}，不换内容重发，本轮结束")
            return
        msg = gen_topic(state)
        if not msg:
            log("语音失败后文字也没生成，跳过本轮")
            return
        ok2, st2 = send_text(msg, managed_history=True)
        if ok2:
            _remember_send(state, "text_fallback")
        return

    # 文字 + 语音：一次发出，历史记两段文本
    msg = gen_topic(state)
    vtext = gen_voice_text(state)
    if not msg or not vtext:
        log("双发缺少文字或语音稿，跳过本轮")
        return
    try:
        p, spoken = synth_voice_ex(vtext)
        parts = [
            {"type": "plain", "text": msg},
            {"type": "record", "attachment_id": upload_file(p),
             "filename": os.path.basename(p)},
        ]
        ok, st = send_parts(parts, managed_history=True,
                            history_text=(msg + " " + spoken).strip())
    except Exception as e:  # noqa: BLE001
        log(f"双发链路异常: {e}")
        ok, st = False, "failed"
    if ok:
        _remember_send(state, "both")
        return
    log(f"双发没发出去（state={st}）")
    if st in ("unknown", "in_progress", "dropped", "skipped"):
        log(f"状态={st}，不换内容重发，本轮结束")
        return
    msg2 = gen_topic(state)
    if not msg2:
        log("双发失败后文字也没生成，跳过本轮")
        return
    ok2, st2 = send_text(msg2, managed_history=True)
    if ok2:
        _remember_send(state, "text_fallback")


def refresh_date():
    """每天把数据库配置里的「今天是几号」更新掉（数据库是唯一事实来源）。"""
    import sqlite3

    today = datetime.now().strftime("%Y年%m月%d日")
    try:
        db = sqlite3.connect(DB_PATH)
        row = db.execute(
            "select system_prompt from personas where persona_id='ajing'").fetchone()
        if not row or not row[0]:
            db.close()
            return
        content = row[0]
        new = re.sub(r"现在是\d{4}年\d{1,2}月\d{1,2}日", f"现在是{today}", content)
        if new != content:
            db.execute("UPDATE personas SET system_prompt=? WHERE persona_id='ajing'",
                       (new,))
            db.commit()
            try:
                Path(PERSONA_FILE).write_text(new, encoding="utf-8")
            except Exception:
                pass
            log(f"日期已刷新: {today}")
        db.close()
    except Exception as e:  # noqa: BLE001
        log(f"日期刷新失败: {str(e)[:60]}")


def _resume_delay(state):
    """重启后若刚发过消息，先按间隔等一等，避免一重启就连发（P1-11）。"""
    last = int(state.get("last_sent") or 0)
    if not last:
        return 0
    gap = random.randint(MIN_INTERVAL, MAX_INTERVAL)
    left = last + gap - int(time.time())
    return max(0, left)


def main():
    if not acquire_pid_lock():
        if "--once" in sys.argv:
            log("--once 未执行：已有实例持锁运行（不是成功）")
            return 2
        return 0
    log(f"Bot管家启动 (PID {os.getpid()})")
    wait_for_astrbot()
    refresh_date()
    state = load_state()
    if "--once" in sys.argv:
        now = datetime.now()
        if not (ACTIVE_HOURS[0] <= now.hour < ACTIVE_HOURS[1]):
            log(f"静默时段 {ACTIVE_HOURS[0]}-{ACTIVE_HOURS[1]}，--once 跳过主动消息")
            return 0
        act(state)
        log("单次执行完毕")
        return

    delay = _resume_delay(state)
    if delay > 0:
        log(f"上次发送在 {datetime.fromtimestamp(int(state['last_sent'])):%m-%d %H:%M}，"
            f"先等 {delay // 60} 分钟再开始")
        time.sleep(delay)

    while True:
        now = datetime.now()
        refresh_date()
        if ACTIVE_HOURS[0] <= now.hour < ACTIVE_HOURS[1]:
            _t0 = time.time()
            try:
                act(state)
            except Exception as e:  # noqa: BLE001
                log(f"任务异常: {e}")
            finally:
                _cost = time.time() - _t0
                if _cost > 120:
                    log(f"本轮耗时 {_cost:.0f}s（>120s，检查网络/接口超时）")
        interval = random.randint(MIN_INTERVAL, MAX_INTERVAL)
        log(f"下次行动: {interval // 60} 分钟后")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())

