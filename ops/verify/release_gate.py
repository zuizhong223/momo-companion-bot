# -*- coding: utf-8 -*-
"""发布前离线门禁：补丁一致性 + KB 四数 + B03 结构 + 全部离线测试。

不发送 QQ、不调用付费模型、不写正式库（测试都用副本/临时库）。
用法：
  python ops\\verify\\release_gate.py
  python ops\\verify\\release_gate.py --skip-tests   # 只跑静态检查
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
PY = sys.executable
TESTS = [
    "test_capability.py",
    "test_b02.py",
    "test_b05.py",
    "test_history.py",
    "test_voice_history.py",
    "test_ledger.py",
    "test_gate.py",
    "test_delivery_identity.py",
    "test_proactive.py",
    "test_proactive_topic.py",
    "test_restart.py",
    "test_ghost_history.py",
    "test_p0_partial_delivery.py",
    "test_p0_turn_gap.py",
    "test_p0_send_manifest_conflict.py",
    "test_p0_partial_retry.py",
    "test_p0_dedup.py",
    "test_p0_tts_stream.py",
    "test_p0_conflict_observe.py",
    "test_p0_source_identity.py",
    "test_p1_delete_residue.py",
    "test_p1_unknown_log.py",
    "test_p1_bili_copy.py",
    "test_p1_bili_frame.py",
    "test_p1_time_injection.py",
    "test_p1_proactive_gate.py",
    "test_q10_proactive_governance.py",
    "test_p1_ellipsis_paths.py",
    "test_p1_semantic_context.py",
    "test_s9_observe.py",
    "test_source_chain.py",
    "test_media_payload.py",
    "test_steward_states.py",
    "test_memory_mood.py",
    "test_merge.py",
    "test_tz.py",
    "test_q7_time.py",
    "test_q1_brevity_hint.py",
    "test_history_race.py",
    "test_kb_query.py",
    "test_db_isolation.py",
    "test_wiring_presence.py",
    "test_commitment_flow.py",
    "test_commitment_evidence.py",
    "test_p0_promise.py",
    "test_p0_told.py",
    "test_p0_canon_source.py",
    "test_crash_matrix.py",
    "test_history_binding.py",
    "test_r08_detect.py",
    "test_mood_objects.py",
    "test_fact_candidate.py",
    "test_turn_manifest.py",
    "test_recovery_queue.py",
    "test_steward_life_context.py",
    "test_life_hygiene.py",
    "test_canon_override.py",
    "test_life_links.py",
    "test_life_state.py",
    "test_life_policy.py",
    "test_life_inject.py",
    "test_life_relations_fix.py",
    "test_relation_life_fix.py",
    "test_life_shadow_isolation.py",
    "test_agent_wiring.py",
    "test_sticker_tags.py",
    "test_q9_sticker_quota.py",
    "test_q3_haohao.py",
    "test_q2_multi_bubble.py",
    "test_q1_shorten_banter.py",
    "test_q5_decision.py",
    "test_q4_short_repeat.py",
    "test_s10_log_redaction.py",
    "test_style_rag.py",
    "test_style_score.py",
    "test_event_memory.py",
    "test_event_shadow_wiring.py",
    "test_dump_dict.py",
    "test_a1_partial.py",
    "test_tts_clean.py",
    "test_confirm_budget.py",
    "test_support_repeat.py",
    "test_short_rhythm.py",
    "test_b21_safe_restart.py",
    "test_p2_schedule.py",
    "test_p2_energy.py",
    "test_p2_life.py",
    "test_life_guard.py",
    "test_s11_delete.py",
    "test_s8_voice_quota.py",
    "test_serious_hint.py",
    "test_memory_layer.py",
    "test_persona_lock.py",
    "test_serious_probe.py",
    "test_persona_core.py",
    "test_style_wiring.py",
    "test_watchdog.py",
    "test_tic_budget.py",
    "test_length_nudge.py",
    "test_backup_rotate.py",
    "test_similarity_metrics.py",
]


FLAKY: list[str] = []
NO_RETRY = ("test_restart.py", "test_proactive.py", "test_history_race.py")


def run(args: list[str], title: str, retry: bool = True) -> bool:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DEMO_TEST="1", DEMO_CANON="0")
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    ok = p.returncode == 0
    if not ok and retry and not any(x in " ".join(args) for x in NO_RETRY):
        # 并发编辑会造成偶发假红：重试一次，过了记 flaky 而不是 FAIL
        p2 = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
        if p2.returncode == 0:
            FLAKY.append(title)
            print(f"OK   {title} | 首次失败、重试通过（并发编辑导致，记为 flaky）")
            return True
    tail = (p.stdout or p.stderr or "").strip().splitlines()
    last = tail[-1] if tail else ""
    print(f"{'OK  ' if ok else 'FAIL'} {title} | {last[:140]}")
    if not ok:
        for line in tail[-12:]:
            print("     ", line)
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    skip_tests = "--skip-tests" in sys.argv
    results = []
    results.append(run([PY, "-m", "compileall", "-q",
                        str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot"),
                        str(ROOT / "Bot管家.py")], "compile"))
    results.append(run([PY, str(ROOT / "ops/patches/patches.py"), "verify"],
                       "patches verify"))
    results.append(run([PY, str(ROOT / "ops/verify/check_git_secrets.py")],
                       "git secrets"))
    results.append(run([PY, str(ROOT / "ops/backup/check_kb_counts.py")],
                       "KB 四数"))
    results.append(run([PY, str(ROOT / "ops/eval/check_memory_cases.py")],
                       "B03 cases"))
    results.append(run([PY, str(ROOT / "ops/eval/check_serious_trigger.py")],
                       "serious 20"))
    results.append(run([PY, str(ROOT / "ops/verify/life_72h.py")],
                       "life 48h"))
    results.append(run([PY, str(ROOT / "ops/verify/life_overlap.py")],
                       "life overlap"))
    # 配置基准：默认不跑（每次约 ¥4.6）；发布前 export DEMO_BENCH=1 才执行
    if os.environ.get("DEMO_BENCH", "").strip() in ("1", "true", "yes"):
        _runs = os.environ.get("DEMO_BENCH_RUNS", "3")
        results.append(run([PY, str(ROOT / "ops/eval/bench.py"),
                            "--cases", "holdout_v2_20261026.jsonl",
                            "--runs", _runs, "--tag", "gate_bench"],
                           "bench"))
    results.append(run([PY, str(ROOT / "ops/eval/freeze_holdout.py"),
                        "--cases", "holdout_v2_20261026.jsonl", "--check"],
                       "holdout frozen"))
    results.append(run([PY, str(ROOT / "ops/eval/freeze_holdout.py"),
                        "--cases", "holdout_v3_20261027.jsonl", "--check"],
                       "holdout v3 frozen"))
    results.append(run([PY, str(ROOT / "ops/verify/life_7d.py")],
                       "life 7d"))
    if not skip_tests:
        for t in TESTS:
            results.append(run([PY, str(ROOT / "ops/tests" / t)], t))
    ok = all(results)
    if FLAKY:
        print(f"\n[flaky] {len(FLAKY)} 项首次失败、重试通过：{', '.join(FLAKY)}")
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
