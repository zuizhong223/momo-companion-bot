# -*- coding: utf-8 -*-
"""连发合并（P0-3）离线测试：不连 QQ，只喂合成事件。"""
import asyncio
import sys
import time
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.message.components import At, Image, Plain  # noqa: E402
from astrbot.core.platform import (  # noqa: E402
    AstrBotMessage,
    AstrMessageEvent,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.core.pipeline.message_merge import SessionMerger  # noqa: E402

META = PlatformMetadata(name="aiocqhttp", description="t", id="aiocqhttp")
SELF = "100000002"


def make_event(text=None, img=False, at=False, sender="100000000",
               session="100000000", mtype=MessageType.FRIEND_MESSAGE,
               mid="1"):
    abm = AstrBotMessage()
    abm.type = mtype
    abm.self_id = SELF
    abm.sender = MessageMember(user_id=sender, nickname="tester")
    abm.session_id = session
    abm.message_id = mid
    abm.timestamp = int(time.time())
    parts = []
    if at:
        parts.append(At(qq=SELF, name="Bot"))
    if text:
        parts.append(Plain(text=text))
    if img:
        parts.append(Image(file="x.jpg", url="http://example.com/x.jpg"))
    abm.message = parts
    abm.message_str = text or ("[图片]" if img else "")
    abm.raw_message = {"message_id": mid}
    ev = AstrMessageEvent(
        message_str=abm.message_str, message_obj=abm,
        platform_meta=META, session_id=session,
    )
    ev.umo_tag = abm.session_id
    return ev


async def main():
    got = []
    clock = {"t": 0.0}

    async def runner(ev):
        got.append((ev.message_str, [type(c).__name__ for c in ev.message_obj.message],
                    round(time.monotonic() - clock["t"], 2)))
        await asyncio.sleep(0.3)

    rg = {"ok": True}

    async def case1():
        got.clear()
        merger = SessionMerger(runner, window_s=0.2, max_wait_s=1.0)
        for i in range(3):
            merger.submit(make_event(f"第{i}条", mid=str(i)))
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.6)
        ok = len(got) == 1 and got[0][0] == "第0条 第1条 第2条"
        print(f"{'OK  ' if ok else 'FAIL'} 三条连发合并: {got}")
        return ok

    async def case2():
        got.clear()
        merger = SessionMerger(runner, window_s=0.2, max_wait_s=1.0)
        merger.submit(make_event("A1", sender="111", mid="a1"))
        await asyncio.sleep(0.05)
        merger.submit(make_event("B1", sender="222", mid="b1"))
        await asyncio.sleep(0.8)
        ok = len(got) == 2 and got[0][0] == "A1" and got[1][0] == "B1"
        print(f"{'OK  ' if ok else 'FAIL'} 不同发送者不合并: {got}")
        return ok

    async def case3():
        got.clear()
        merger = SessionMerger(runner, window_s=5.0, max_wait_s=6.0)
        merger.submit(make_event("你在吗", mid="1"))
        await asyncio.sleep(0.05)
        merger.submit(make_event("看这个", img=True, mid="2"))
        await asyncio.sleep(0.05)
        merger.submit(make_event("", at=True, mid="3"))
        await asyncio.sleep(0.5)
        ok = (len(got) == 1
              and got[0][0] == "你在吗 看这个"
              and got[0][1] == ["Plain", "Image", "At"])
        print(f"{'OK  ' if ok else 'FAIL'} @ 立即冲出 + 段序保留: {got}")
        return ok

    async def case4():
        got.clear()
        merger = SessionMerger(runner, window_s=3.0, max_wait_s=0.35)
        for i in range(4):
            merger.submit(make_event(f"m{i}", mid=str(i)))
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.8)
        ok = len(got) == 1
        print(f"{'OK  ' if ok else 'FAIL'} 6s 上限（这里缩到 0.35s）: {got}")
        return ok

    async def case5():
        got.clear()
        merger = SessionMerger(runner, window_s=0.2, max_wait_s=1.0)
        merger.submit(make_event("第一波", mid="1"))
        await asyncio.sleep(0.25)          # 第一波已开始执行（runner 0.3s）
        merger.submit(make_event("第二波", mid="2"))
        merger.submit(make_event("第三波", mid="3"))
        await asyncio.sleep(1.2)
        ok = (len(got) == 2 and got[1][0] == "第二波 第三波")
        print(f"{'OK  ' if ok else 'FAIL'} 生成中排队后合并: {got}")
        return ok

    async def case6():
        got.clear()
        merger = SessionMerger(runner, window_s=0.2, max_wait_s=1.0)
        merger.submit(make_event("私聊"))
        merger.submit(make_event("群聊", session="100000001",
                                 mtype=MessageType.GROUP_MESSAGE))
        await asyncio.sleep(0.6)
        ok = len(got) == 2
        print(f"{'OK  ' if ok else 'FAIL'} 会话隔离: {got}")
        return ok

    async def case7():
        got.clear()
        merger = SessionMerger(runner, window_s=0.2, max_wait_s=1.0)
        merger.submit(make_event("单条"))
        await asyncio.sleep(0.5)
        ok = len(got) == 1 and got[0][0] == "单条"
        print(f"{'OK  ' if ok else 'FAIL'} 单条原样直通: {got}")
        return ok

    for fn in (case1, case2, case3, case4, case5, case6, case7):
        clock["t"] = time.monotonic()
        rg["ok"] = await fn() and rg["ok"]
    print("\nRESULT:", "PASS" if rg["ok"] else "FAIL")
    return 0 if rg["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
