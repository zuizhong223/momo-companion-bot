# -*- coding: utf-8 -*-
"""风格打分器单元测试：角色的真话应显著高于 AI 套话。"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"D:\demo\ops\eval")))
from style_score import StyleModel  # noqa: E402

m = StyleModel()
her = m.corpus[:200]
generic = [
    "我明白了，有什么需要帮助的吗", "作为一个人工智能语言模型，我无法",
    "好的，我已经了解了你的需求", "这个问题需要从多个角度来分析",
    "请你详细描述一下具体的情况", "我会尽力为你提供帮助",
    "首先我们需要明确目标和范围", "根据你的描述我建议你",
    "感谢你的提问，我来解释一下", "你可以尝试以下几个步骤",
]
h = statistics.mean(m.score(t)["final"] for t in her)
g = statistics.mean(m.score(t)["final"] for t in generic)
hp = statistics.mean(m.score(t)["ppl"] for t in her)
gp = statistics.mean(m.score(t)["ppl"] for t in generic)
ok = h > g + 0.15 and hp < gp / 3
print(f"角色的均分 {h:.3f} vs 套话 {g:.3f}；ppl {hp:.1f} vs {gp:.1f}")
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
