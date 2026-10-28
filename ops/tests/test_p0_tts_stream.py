# -*- coding: utf-8 -*-
"""P0/A4：TTS 流必须 status=2 才算完成；业务错误不能接受半截音频；预处理统一。"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def sse(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()


def sse_raw(payload: dict) -> bytes:
    """没有尾随空行的最后一个 data: 包。"""
    return ("data: " + json.dumps(payload, ensure_ascii=False)).encode()


class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""


class FakeResponse:
    def __init__(self, chunks):
        self.content = FakeContent(chunks)

    def raise_for_status(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, chunks, **kwargs):
        self.chunks = chunks

    def post(self, *args, **kwargs):
        return FakeResponse(self.chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def load_provider():
    from astrbot.core.provider.sources import minimax_tts_api_source as mod

    cfg = {
        "api_key": "test",
        "api_base": "https://example.invalid/v1/t2a_v2",
        "model": "speech-2.8-hd",
        "minimax-group-id": "g",
        "minimax-voice-id": "v",
    }
    return mod, mod.ProviderMiniMaxTTSAPI(cfg, {})


def run_stream(provider, chunks):
    async def collect():
        return [x async for x in provider._call_tts_stream("测试")]

    return asyncio.run(collect())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mod, provider = load_provider()

    print("\n[1] 流式必须 status=2")
    mod.aiohttp.ClientSession = lambda **kw: FakeSession([
        sse({"data": {"audio": "aa", "status": 1},
             "base_resp": {"status_code": 0}}),
    ])
    try:
        run_stream(provider, [])
        check(False, "status=1 + EOF 应失败")
    except Exception as exc:  # noqa: BLE001
        check("status=2" in str(exc) or "不完整" in str(exc),
              "status=1 + EOF 抛错", str(exc))

    print("\n[2] 流内业务错误不能接受前段")
    mod.aiohttp.ClientSession = lambda **kw: FakeSession([
        sse({"data": {"audio": "aa", "status": 1},
             "base_resp": {"status_code": 0}}),
        sse({"data": {"audio": "", "status": 2},
             "base_resp": {"status_code": 1000, "status_msg": "boom"}}),
    ])
    try:
        run_stream(provider, [])
        check(False, "业务错误应失败")
    except Exception as exc:  # noqa: BLE001
        check("1000" in str(exc) or "业务错误" in str(exc),
              "业务错误抛错", str(exc))

    print("\n[3] 正常完整流 + final extra_info 不被跳过")
    mod.aiohttp.ClientSession = lambda **kw: FakeSession([
        sse({"data": {"audio": "aa", "status": 1},
             "base_resp": {"status_code": 0}}),
        sse({"data": {"audio": "bb", "status": 2},
             "extra_info": {"audio_length": 1},
             "base_resp": {"status_code": 0, "status_msg": "success"}}),
    ])
    got = run_stream(provider, [])
    check(got == ["aa", "bb"], "两段音频都收到", str(got))

    print("\n[3b] 最后一个 data 包没有尾随空行")
    mod.aiohttp.ClientSession = lambda **kw: FakeSession([
        sse({"data": {"audio": "aa", "status": 1},
             "base_resp": {"status_code": 0}}),
        sse_raw({"data": {"audio": "bb", "status": 2},
                 "base_resp": {"status_code": 0}}),
    ])
    got = run_stream(provider, [])
    check(got == ["aa", "bb"], "EOF 尾包也解析", str(got))

    print("\n[4] 请求体排除聚合音频，避免最后一包重复")
    body = json.loads(provider._build_tts_stream_body("测试"))
    check(body.get("stream_options", {}).get("exclude_aggregated_audio") is True,
          "exclude_aggregated_audio=true", str(body.get("stream_options")))

    print("\n[5] 预处理幂等 + 两个入口一致")
    check(provider.prepare_text("在吗") == "在吗？", "在吗→在吗？")
    check(provider.prepare_text(provider.prepare_text("在吗")) == "在吗？",
          "在吗幂等")
    for raw, want in (("呵呵呵呵", "呵呵。"), ("嘻嘻嘻嘻", "嘻嘻。"),
                      ("哈哈哈哈哈哈", "哈哈哈。")):
        check(provider.prepare_text(raw) == want,
              f"provider {raw}->{want}", provider.prepare_text(raw))

    spec = importlib.util.spec_from_file_location("demo_butler_p0_tts",
                                                  ROOT / "butler.py")
    butler = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = butler
    spec.loader.exec_module(butler)
    for raw in ("呵呵呵呵", "嘻嘻嘻嘻", "哈哈哈哈哈哈", "在吗"):
        check(butler._speech_text(raw) == provider.prepare_text(raw),
              f"两入口一致: {raw}", f"{butler._speech_text(raw)} != {provider.prepare_text(raw)}")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
