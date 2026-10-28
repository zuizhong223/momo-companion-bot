import sys
sys.path.insert(0,r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
from astrbot.core.pipeline.result_decorate.stage import support_sleep_fallback as f
cases=[
 ("我认可你","快十二点了 早点睡吧你"),
 ("我关心你了","都快十二点了 睡吧（"),
 ("我认可你。你关心我吗","好好好 睡觉去（"),
 ("我认可你","我也认可你"),
 ("我认可你，我要睡了","早点睡）"),
 ("你在干嘛","快睡吧"),
]
for u,r in cases: print(u,"|",r,"->",repr(f(u,r)))
print("RESULT", "PASS" if f("我认可你","睡吧") and f("我认可你","我也认可你") is None else "FAIL")
