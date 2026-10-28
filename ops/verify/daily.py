#!/usr/bin/env python

# -*- coding: utf-8 -*-

"""每日体检：一屏看完"机器人今天有没有异常"。



检查项（都不改动任何东西）：

  1. 服务：AstrBot / 管家 / 端口 6199·22877 / QQ

  2. 台账：sent·failed·unknown·dropped·sending、历史 pending/partial、payload_mismatch

  3. 补丁：patches verify（包内与实例是否一致）

  4. 冻结集：holdout 是否被改

  5. 配置漂移：当前 sha 与基准是否一致

  6. 影子：样本数、一致率、延迟

  7. 磁盘与备份：包数、体积、最新包时间



用法：

  python ops\\verify\\daily.py            # 打印 + 写 ops/verify/daily_YYYYMMDD.md

  python ops\\verify\\daily.py --quiet    # 只写文件

"""

from __future__ import annotations



import argparse

import datetime

import json

import os

import sqlite3

import subprocess

import sys

from pathlib import Path



ROOT = Path(r"D:/demo")

INST = ROOT / "instances/11111111-1111-4111-8111-111111111111"

CORE = INST / "core"

DB = CORE / "data/data_v4.db"

PY = INST / "venv/Scripts/python.exe"

SHADOW = CORE / "data/serious_shadow.jsonl"

BASELINE = ROOT / "ops/eval/reports/bench_base_1027.json"

OUT = ROOT / "ops/verify"

PERSONA = ROOT / "persona.txt"



RED = []





def _life_lines() -> list[str]:
    """生活层 / 记忆层的一行摘要（只读）。"""
    import sqlite3 as _sq
    from datetime import date as _date

    db = CORE / "data/data_v4.db"
    out: list[str] = []
    day = _date.today().isoformat()
    try:
        con = _sq.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ev = con.execute(
                "SELECT status,count(*) FROM yy_life_event WHERE date(COALESCE(end_at,start_at))=?"
                " GROUP BY status", (day,)).fetchall()
            m = dict(ev)
            out.append(f"- 今天事件 {sum(m.values())} 个（完成 {m.get('done', 0)} / "
                       f"没做成 {m.get('missed', 0)} / 计划中 {m.get('planned', 0) + m.get('active', 0)}）")
            try:
                cm = con.execute("SELECT count(*) FROM yy_life_commitment"
                                 " WHERE status IN ('planned','active')").fetchone()[0]
                out.append(f"- 未兑现承诺 {cm} 条")
            except _sq.OperationalError:
                pass
            try:
                pl = con.execute("SELECT count(*) FROM yy_life_place").fetchone()[0]
                out.append(f"- 地点记录 {pl} 条")
            except _sq.OperationalError:
                pass
            try:
                mm = con.execute("SELECT count(*) FROM yy_memory WHERE status='active'").fetchone()[0]
                cur = con.execute("SELECT cursor FROM yy_digest_state WHERE id=1").fetchone()
                out.append(f"- 记忆 {mm} 条（摘要游标 {cur[0] if cur else '-'}）")
            except _sq.OperationalError:
                pass
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        out.append(f"- 读取失败：{e}")
    return out


def sh(cmd: list[str]) -> str:

    try:

        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",

                           errors="replace", timeout=180, cwd=str(ROOT))

        return (p.stdout or "") + (p.stderr or "")

    except Exception as e:  # noqa: BLE001

        return f"<失败 {e}>"





def ps(script: str) -> str:

    return sh(["powershell", "-NoProfile", "-Command", script])





def _notify(text: str, cooldown_path: Path) -> bool:

    """用 AstrBot 的 OpenAPI 给机器人发一条提醒；带冷却，避免刷屏。"""

    try:

        import time as _t



        if cooldown_path.exists():

            last = float(cooldown_path.read_text(encoding="utf-8") or 0)

            if _t.time() - last < 6 * 3600:

                return False

        sys.path.insert(0, str(ROOT))

        import local_keys as lk

        import urllib.request



        body = {"umo": "aiocqhttp:FriendMessage:100000000",

                "message": [{"type": "plain", "text": text}]}

        req = urllib.request.Request(

            "http://127.0.0.1:22877/api/v1/im/message",

            data=json.dumps(body).encode(),

            headers={"X-API-Key": lk.OPENAPI_KEY, "Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=20) as r:

            raw = r.read().decode("utf-8", "replace")

            ok = r.status == 200

        # HTTP 200 不等于业务已发；有 status 字段时再看业务状态。

        try:

            data = json.loads(raw or "{}")

            if isinstance(data, dict) and str(data.get("status", "")).lower() in (

                    "failed", "error", "false"):

                ok = False

        except ValueError:

            pass

        if ok:

            cooldown_path.write_text(str(_t.time()), encoding="utf-8")

        return ok

    except Exception:  # noqa: BLE001

        return False





def main() -> int:

    ap = argparse.ArgumentParser()

    ap.add_argument("--quiet", action="store_true")

    ap.add_argument("--notify", action="store_true", help="有红灯就发一条 QQ 提醒（6 小时冷却）")

    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    today = datetime.date.today()

    lines: list[str] = [f"# 每日体检 · {today} {datetime.datetime.now():%H:%M}", ""]



    def add(section: str, body: list[str]) -> None:

        lines.append(f"## {section}")

        lines.extend(body)

        lines.append("")



    # 1 服务

    # 注意：PS 5.1 里单对象的 .Count 是空串，端口判断必须用 if 写法

    out = ps("@( (Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "

             "Where-Object { $_.CommandLine -like '*main.py*' }).Count; "

             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "

             "Where-Object { $_.CommandLine -like '*Bot管家.py*' }).Count; "

             "if (Get-NetTCPConnection -LocalPort 6199 -State Listen -ErrorAction SilentlyContinue) {1} else {0}; "

             "if (Get-NetTCPConnection -LocalPort 22877 -State Listen -ErrorAction SilentlyContinue) {1} else {0} ) "

             "-join \",\"")

    nums = [x.strip() for x in out.strip().split(",") if x.strip().isdigit()]

    ab, mg, p61, p22 = (nums + ["0"] * 4)[:4]

    ok_svc = int(ab) >= 1 and int(mg) >= 1 and int(p22) >= 1

    if not ok_svc:

        RED.append("服务不完整")

    add("服务", [f"- AstrBot {ab} · 管家 {mg} · 6199 {'OK' if int(p61) else '连接未建立'} · "

                 f"22877 {'OK' if int(p22) else '未监听'}"])



    # 2 台账

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    try:

        def n(sql, *a):

            return con.execute(sql, a).fetchone()[0]

        counts = {s: n("select count(*) from yy_send_task where state=?", s)

                  for s in ("sent", "failed", "unknown", "dropped", "sending")}

        hist = {h: n("select count(*) from yy_send_task where history_state=? and state='sent'", h)

                for h in ("pending", "partial", "needs_target")}

        pm = n("select count(*) from yy_send_task where error like '%payload_mismatch%'")

        total = sum(counts.values())

        rate = counts["sent"] / total if total else 0

    finally:

        con.close()

    if counts["sending"]:

        RED.append(f"sending={counts['sending']}")

    if hist["partial"]:

        RED.append(f"history partial={hist['partial']}")

    add("台账", [f"- sent {counts['sent']} / failed {counts['failed']} / unknown {counts['unknown']} "

                 f"/ dropped {counts['dropped']} / sending {counts['sending']}（成功率 {rate:.1%}）",

                 f"- 历史 pending {hist['pending']} · partial {hist['partial']} · "

                 f"needs_target {hist['needs_target']} · payload_mismatch {pm}"])



    # 3 补丁

    po = sh([str(PY), "ops/patches/patches.py", "verify"])

    tail = [ln for ln in po.splitlines() if ln.strip()][-1:] or ["<无输出>"]

    if "包内" in tail[0] and "完好" not in tail[0]:

        RED.append("补丁不一致")

    add("补丁", [f"- {tail[0]}"])



    # 4 冻结集

    hf = sh([str(PY), "ops/eval/freeze_holdout.py", "--cases", "holdout_v2_20261026.jsonl", "--check"])

    hf_ok = "PASS" in hf

    if not hf_ok:

        RED.append("冻结集被修改")

    add("冻结集", [f"- holdout_v2 {'未改动' if hf_ok else '**被改过**'}"])



    # 5 配置漂移

    try:

        import hashlib



        sha = hashlib.sha256(PERSONA.read_text(encoding="utf-8").encode()).hexdigest()[:16]

        base_sha = None

        if BASELINE.exists():

            base_sha = json.loads(BASELINE.read_text(encoding="utf-8")).get("persona", {}).get("file_sha")

        drift = base_sha and base_sha != sha

        add("配置", [f"- 当前 sha `{sha}`" + (f"（基准 `{base_sha}`，**已漂移**）" if drift else "（与基准一致）")])

        if drift:

            lines.append("> 配置变了：任何和历史分数的比较都要重跑 bench。\n")

    except Exception as e:  # noqa: BLE001

        add("配置", [f"- 读取失败：{e}"])



    # 5b 配置锁

    try:

        lock_p = ROOT / "ops/persona.lock"

        if lock_p.exists():

            lk = json.loads(lock_p.read_text(encoding="utf-8"))

            add("配置锁", [f"- **被 {lk.get('who')} 持有**（{lk.get('since')}）：{lk.get('why')}"])

        else:

            add("配置锁", ["- 空闲"])

    except Exception as e:  # noqa: BLE001

        add("配置锁", [f"- 读取失败：{e}"])



    # 5c 生活层 / 记忆层
    add("生活层 / 记忆层", _life_lines())

    # 6 影子（并生成回放摘要，作为"要不要开 on"的日报）

    if SHADOW.exists():

        rows = [json.loads(x) for x in SHADOW.read_text(encoding="utf-8").splitlines() if x.strip()]

        ok = [r for r in rows if r.get("llm") is not None]

        hit = [r for r in ok if r["llm"]]

        ms = sorted(r["ms"] for r in ok if isinstance(r.get("ms"), int))

        only_llm = [r for r in ok if r["llm"] and r.get("regex") != "serious"]

        add("影子探针", [f"- 样本 {len(rows)}（可用 {len(ok)}）· 判认真 {len(hit)} · "

                         f"p50 {ms[len(ms)//2] if ms else '-'}ms · max {ms[-1] if ms else '-'}ms",

                         f"- 正则漏、模型抓到 {len(only_llm)} 条"

                         + ("（够 20 条就能决定开不开 on）" if len(only_llm) < 20 else "（样本够了，可以决定）")])

        try:

            sh(["python", str(ROOT / "ops/eval/serious_shadow_report.py")])

        except Exception:  # noqa: BLE001

            pass

    else:

        add("影子探针", ["- 还没有样本"])



    # 7 磁盘与备份

    z = sorted(list((ROOT / "backups").glob("demo_backup_*.zip")) + list((ROOT / "backups").glob("demo_backup_*.7z")), key=lambda p: p.stat().st_mtime)

    size_gb = sum(p.stat().st_size for p in z) / 1024 ** 3

    free = ps("(Get-PSDrive F).Free") 

    free_gb = int(free.strip()) / 1024 ** 3 if free.strip().isdigit() else 0

    add("磁盘与备份", [f"- 备份 {len(z)} 个 / {size_gb:.2f} GB"

                       + (f" · 最新 {z[-1].name}" if z else ""),

                       f"- F 盘剩余 {free_gb:.0f} GB"])

    if free_gb and free_gb < 50:

        RED.append("磁盘剩余 < 50GB")



    # 结论

    lines.append("## 结论")

    lines.append(f"- {'**有 ' + str(len(RED)) + ' 项红灯**：' + '、'.join(RED) if RED else '全绿'}")

    text = "\n".join(lines) + "\n"

    OUT.mkdir(parents=True, exist_ok=True)

    f = OUT / f"daily_{today:%Y%m%d}.md"

    f.write_text(text, encoding="utf-8")

    if not args.quiet:

        print(text)

    if RED and args.notify:

        msg = f"[体检] {len(RED)} 项红灯：" + "、".join(RED) + f"（详见 ops/verify/{f.name}）"

        sent = _notify(msg, ROOT / "ops/alerts/.daily_notify")

        print(f"[daily] 通知{'已发' if sent else '未发（冷却中或发送失败）'}")

        if not sent:

            # B14：QQ 不可用时，至少把红灯落到本地文件，不依赖故障渠道。

            alarm = ROOT / "ops/alerts" / f"ALERT_{datetime.datetime.now():%Y%m%d_%H%M}.md"

            alarm.write_text(msg + "\n", encoding="utf-8")

            print(f"[daily] 已写本地告警 {alarm.name}")

    print(f"[daily] 已写 {f.name}；红灯 {len(RED)} 项")

    return 0 if not RED else 1





if __name__ == "__main__":

    sys.exit(main())


