# -*- coding: utf-8 -*-
"""只读：打印关键配置值。"""
import json

CFG = r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\cmd_config.json"


def show(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                show(v, p)
            elif isinstance(v, list) and len(json.dumps(v, ensure_ascii=False)) > 160:
                print(f"{p} = <list len={len(v)}>")
            else:
                print(f"{p} = {json.dumps(v, ensure_ascii=False)[:160]}")


with open(CFG, encoding="utf-8-sig") as f:
    cfg = json.load(f)

print("== top keys ==")
for k in cfg:
    print(" ", k)

print("\n== provider_settings ==")
ps = cfg.get("provider_settings", {})
for k in ("enable", "persona", "default_personality", "persona_id", "kb_names",
          "streaming_response", "max_turns", "use_func_tool", "proactive_capability",
          "buffer_intermediate_messages", "agent_runner"):
    print(f"  {k} = {json.dumps(ps.get(k), ensure_ascii=False)[:300]}")

print("\n== agent_runner ==")
print(json.dumps(cfg.get("agent_runner", {}), ensure_ascii=False, indent=1)[:3000])

print("\n== platform_settings ==")
print(json.dumps(cfg.get("platform_settings", {}), ensure_ascii=False, indent=1)[:1200])

print("\n== provider ids ==")
for p in cfg.get("provider", []):
    print(" ", p.get("id"), p.get("type"), (p.get("model_config") or {}).get("model"))

print("\n== timezone ==", cfg.get("timezone"))
print("== log_file_enable ==", cfg.get("log_file_enable"))
print("== enable_output_content_filter ==", cfg.get("enable_output_content_filter"))
print("== dashboard ==", json.dumps(cfg.get("dashboard"), ensure_ascii=False)[:300])
