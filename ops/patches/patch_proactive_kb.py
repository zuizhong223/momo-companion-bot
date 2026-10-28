# -*- coding: utf-8 -*-
"""P1-2：给主动消息加知识库检索。

在 `_build_prompt` 之前查一次 KB（query = 历史里最后一条用户消息），
命中的片段以"可以顺带提一句的旧事"的形式放进 **user 侧**（不进 system，
避免被当成指令），并限制长度。

用法：python ops\\personas\\patch_proactive_kb.py [--apply]
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

SRC = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111"
           r"\core\astrbot\dashboard\services\proactive_service.py")
OUT = Path(r"D:/demo\ops\patches")

HELPER = '''
    async def _kb_hint(self, umo: str, history: list) -> str:
        """按最后一条用户消息查一次知识库，返回一小段"可以提一句的旧事"。"""
        query = ""
        for m in reversed(history or []):
            if m.get("role") == "user":
                txt = " ".join(p.get("text", "") for p in (m.get("content") or [])
                               if isinstance(p, dict))
                txt = txt.split("<system_reminder>")[0].strip()
                if txt:
                    query = txt[:60]
                break
        if not query:
            return ""
        try:
            from astrbot.core.knowledge_base.knowledge_bases import (
                retrieve_knowledge_base,
            )

            text = await retrieve_knowledge_base(query, umo, self.ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[主动同源] 知识库检索失败（忽略）: {e}")
            return ""
        if not text:
            return ""
        text = text.strip()
        if len(text) > 300:
            text = text[:300] + "…"
        logger.info(f"[主动同源] 知识库命中 {len(text)} 字（query={query[:20]!r}）")
        return text

'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    s = SRC.read_text(encoding="utf-8")
    if "_kb_hint" in s:
        print("已经打过这个补丁")
        return 0

    # 1) 插入 helper（放在 generate 前面）
    anchor = "    # ---------- 主入口 ----------\n    async def generate("
    assert anchor in s
    s = s.replace(anchor, HELPER + anchor, 1)

    # 2) generate 里调用它，并把结果塞进 extra_parts
    old = "        system_prompt, dialogs, history, extra_parts = self._build_prompt(info)"
    new = ("        system_prompt, dialogs, history, extra_parts = self._build_prompt(info)\n"
           "        kb_hint = await self._kb_hint(umo, history)\n"
           "        if kb_hint:\n"
           "            from astrbot.core.agent.message import TextPart\n"
           "\n"
           "            extra_parts.append(TextPart(text=(\n"
           "                \"（下面是可以顺带提一句的旧事，只当参考，不要照抄、不要明说是资料：\\n\"\n"
           "                + kb_hint + \"）\")))")
    assert old in s
    s = s.replace(old, new, 1)

    if not args.apply:
        print("预览 OK（未写入）；加 --apply 生效")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%m%d_%H%M%S")
    shutil.copy2(SRC, OUT / f"proactive_service.py.bak.{ts}")
    SRC.write_text(s, encoding="utf-8")
    print(f"已写入；备份 ops/patches/proactive_service.py.bak.{ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
