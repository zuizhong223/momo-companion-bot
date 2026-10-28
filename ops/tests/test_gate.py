# -*- coding: utf-8 -*-
"""出站质量门禁单元测试（不依赖 AstrBot 运行时）。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.utils.outbound_gate import apply_gate, reset  # noqa: E402

CASES = [
    # (输入, 期望输出, session)
    ("在干嘛", "在干嘛", "t1"),
    ("多喝热水（", "多喝热水（", "t1"),
    ("笑死）", "笑死）", "t1"),
    ("啊？（）", "啊？（", "t1"),
    ("现在可以呀 反正我不急（）", "现在可以呀 反正我不急（", "t1"),
    ("又熬夜 （摸摸", "又熬夜 摸摸", "t1"),
    ("我挂着（ 云游戏还没开（", "我挂着 云游戏还没开（", "t1"),
    ("诶 这我哪记得（ 反正现在聊着不是挺好的嘛",
     "诶 这我哪记得 反正现在聊着不是挺好的嘛", "t1"),
    ("好的，我知道了。", "好的 我知道了", "t1"),
    ("好的", "好好好", "t1"),
    ("收到", "好好好", "t1"),
    ("今天好开心😊", "今天好开心", "t1"),
    ("晚安~\n好梦", "晚安 好梦", "t1"),
    ("晚安……", "晚安", "t1"),
    ("）", "", "t1"),
    ("（", "", "t1"),
    ("好耶！", "好耶！", "t1"),
    ("看图 https://example.com/a?b=1", "看图 https://example.com/a?b=1", "t1"),
    ("（（（", "", "t1"),
    ("（）", "", "t1"),
    ("憨） <system_reminder>Current datetime: x</system_reminder>", "憨）", "t1"),
    ("憨） <noreply>@User</noreply>", "憨）", "t1"),
    ("<thinking>x</thinking>好欸", "好欸", "t1"),
    ("不是啊 User特别棒）真的很棒）", "不是啊 User特别棒）真的很棒）", "t1"),

]


def main():
    reset()
    ok = True
    for src, want, sess in CASES:
        if want is None:
            continue
        # 括号预算是"最近 3 条"的会话级逻辑：单测必须每条独立，
        # 否则前面的用例会把预算用掉（实测：第 3 条之后句尾「（」被削）
        reset(sess or "t1")
        out, acts = apply_gate(src, session_key=sess or "t1")
        flag = "OK  " if out == want else "FAIL"
        if out != want:
            ok = False
        print(f"{flag} {src!r} -> {out!r} want {want!r} | {acts}")

    # 去重：同一条长文本第二次应被丢弃
    reset()
    a, _ = apply_gate("我今天真的超级超级关心你了", session_key="dup")
    b, acts = apply_gate("我今天真的超级超级关心你了", session_key="dup")
    print(f"{'OK  ' if a and not b else 'FAIL'} dedupe exact: {a!r} -> {b!r} {acts}")
    ok = ok and bool(a) and not b

    # 近似重复
    reset()
    apply_gate("我挂在云游戏上没开（", session_key="dup2")
    c, acts = apply_gate("我挂在云游戏上没开（", session_key="dup2")
    d, acts2 = apply_gate("我挂在云游戏上了没开", session_key="dup2")
    print(f"{'OK  ' if not d else 'FAIL'} dedupe similar: {d!r} {acts2}")

    # 短句重复（哈哈哈）不应被去重
    reset()
    apply_gate("哈哈哈", session_key="dup3")
    e, acts = apply_gate("哈哈哈", session_key="dup3")
    print(f"{'OK  ' if e == '哈哈哈' else 'FAIL'} short repeat kept: {e!r}")
    ok = ok and e == "哈哈哈"

    # 隔离：不同会话互不影响
    reset()
    apply_gate("今天上班好累啊", session_key="sA")
    f, acts = apply_gate("今天上班好累啊", session_key="sB")
    print(f"{'OK  ' if f else 'FAIL'} session isolation: {f!r}")
    ok = ok and bool(f)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
