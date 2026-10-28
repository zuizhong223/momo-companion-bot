#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份覆盖审计：哪些关键东西**不在**备份包里。

只读：打开最新备份的 zip 清单，和一张"关键资产表"对比，输出 ops/backup/COVERAGE.md。
不删、不打包、不改任何文件。

用法：
  python ops\\backup\\coverage.py
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(r"D:/demo")
BACKUPS = ROOT / "backups"
OUT = ROOT / "ops/backup/COVERAGE.md"

# (显示名, 路径, 丢了会怎样, 恢复难度)
ASSETS = [
    ("会话/配置/台账库", ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db",
     "机器人的记忆全没了", "不可恢复"),
    ("知识库（doc.db + faiss）", ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/knowledge_base",
     "4928 个片段没了，检索失效", "不可恢复"),
    ("配置文本", ROOT / "persona.txt", "性格设定没了", "可从库里配置恢复"),
    ("表情库", ROOT / "表情库", "贴纸通道失效", "可补"),
    ("运维脚本/补丁包", ROOT / "ops", "工具链没了", "可从 git 恢复"),
    ("补丁文件副本", ROOT / "patches", "升级后无法回贴", "可从 git 恢复"),
    ("**音色权重**", ROOT / "GPT-SoVITS/GPT_SoVITS/pretrained_models/qu_s1_e15.ckpt",
     "**机器人的声音没了**", "**不可恢复（唯一副本）**"),
    ("**音色权重 2**", ROOT / "GPT-SoVITS/GPT_SoVITS/pretrained_models/qu_s2G_e8.pth",
     "**机器人的声音没了**", "**不可恢复（唯一副本）**"),
    ("音源（B站原声）", ROOT / "Bot音源", "重训音色的素材没了", "若视频还在可重下"),
    ("本地基座模型", ROOT / "demo_multi_q8.gguf", "本地改写兜底失效", "可重下（3.1GB）"),
    ("本地基座模型 2", ROOT / "demo_v5_q4.gguf", "同上", "可重下（1.8GB）"),
    (".env 密钥", ROOT / ".env", "模型/TTS 全部失效", "平台可再生成（**故意不进包**）"),
    ("NapCat 配置目录", Path(r"D:/demo/napcat\NapCat\NapCat.52230.Shell\versions\9.9.33-52230\resources\app\napcat\config"),
     "QQ 号/端口/token 要重配", "需手填"),
]


def latest_zip() -> Path | None:
    """取最新的**有效**包（可能有人正在写新包）。"""
    z = sorted(list(BACKUPS.glob("demo_backup_*.zip")) + list(BACKUPS.glob("demo_backup_*.7z")), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in z:
        if zipfile.is_zipfile(p):
            return p
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    z = latest_zip()
    if not z:
        print("没有备份包")
        return 1
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
    print(f"对照最新包：{z.name}（{len(names)} 条目）")

    rows = []
    # 目录 -> 包内前缀；文件 -> 用文件名后缀匹配
    DIR_PREFIX = {
        "knowledge_base": "core/data/knowledge_base",
        "ops": "ops/",
        "patches": "patches/",
        "表情库": "表情库/",
        "Bot音源": "Bot音源/",
        "config": "napcat",          # NapCat 配置，基本不会在包里
    }
    for label, path, impact, difficulty in ASSETS:
        exists = path.exists()
        if path.is_dir():
            files = [q for q in path.rglob("*") if q.is_file()]
            size = sum(q.stat().st_size for q in files)
            prefix = DIR_PREFIX.get(path.name)
            covered = bool(prefix) and any(n.startswith(prefix) for n in names) if path.name != "config" else False
        else:
            size = path.stat().st_size if exists else 0
            covered = any(n.endswith(path.name) for n in names)
        rows.append({"label": label, "path": str(path), "size": size,
                     "exists": exists, "covered": covered and exists,
                     "impact": impact, "difficulty": difficulty})
        flag = "覆盖" if (covered and exists) else ("**未覆盖**" if exists else "不存在")
        print(f"  {label:<22} {size/1024/1024:9.0f} MB  {flag}")

    lines = [f"# 备份覆盖审计 · {z.name}", "",
             f"- 最新包：`{z.name}`（{len(names)} 条目）",
             f"- 生成时间：{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}", "",
             "| 资产 | 大小 | 在包里 | 丢了会怎样 | 恢复难度 |",
             "|---|---|---|---|---|"]
    for r in rows:
        size = f"{r['size']/1024/1024:.0f} MB" if r["size"] else "-"
        cov = "是" if r["covered"] else ("**否**" if r["exists"] else "不存在")
        lines.append(f"| {r['label']} | {size} | {cov} | {r['impact']} | {r['difficulty']} |")
    missing = [r for r in rows if r["exists"] and not r["covered"]]
    total = sum(r["size"] for r in missing)
    lines += ["", f"## 未覆盖合计：{len(missing)} 项 / {total/1024**3:.2f} GB", "",
              "**结论**：未覆盖里只有「音色权重」是不可再生的，其余（模型、音源）都能重下或重训；",
              "`.env` 与 NapCat 配置是**故意**不入包的，恢复时手填。"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n未覆盖 {len(missing)} 项 / {total/1024**3:.2f} GB")
    print(f"已写 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
