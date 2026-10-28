# 运维入口（先看这一页）

> 项目根：`D:/demo`　Python：`instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe`
> 所有命令都在项目根执行。

---

## 每天

| 想做什么 | 跑什么 |
|---|---|
| **看机器人今天好不好**（只读） | `python ops\verify\daily.py` |
| 看台账/成功率 | `python ops\verify\observe.py` |
| 清理过期临时文件 | `python ops\verify\cleanup.py --days 3 --apply` |

`daily.py` 已挂计划任务 **DemoBotDaily**（每天 09:00），报告写在 `ops/verify/daily_YYYYMMDD.md`。

---

## 备份与恢复

| 想做什么 | 跑什么 |
|---|---|
| 日常增量备份 | `python ops\backup\backup.py backup` |
| **可启动的**全量备份 | `python ops\backup\backup.py backup --full` |
| 校验最新包 | `python ops\backup\backup.py verify` |
| 演练（不解到线上） | `python ops\backup\backup.py drill` |
| 清理旧包（全量全留） | `python ops\backup\prune.py --keep 30 --apply` |
| **真要恢复** | 看 `ops/backup/RESTORE.md` |

全量备份已挂计划任务 **DemoBotFullBackup**（每 4 周周日 05:10）。

---

## 记忆层（参考 VCP 分层）

| 想做什么 | 跑什么 |
|---|---|
| 把新对话压成经历 | `python ops\memory\digest.py` |
| 看记忆统计 | `python -c "import sys;sys.path.insert(0,'instances/11111111-1111-4111-8111-111111111111/core');from astrbot.core.utils import memory_layer as M;print(M.stats())"` |
| 试召回 | 同上，调 `M.search(umo, '关键词')` |
| 开正式注入 | `DEMO_MEMORY_V2=1`（默认 `shadow`，只记日志） |

每日 04:30 由计划任务 `DemoBotMemoryDigest` 自动跑；日志 `ops\memory\digest.log`。
设计说明见 `ops/MEMORY_LAYER.md`。

## 改完之后

| 想做什么 | 跑什么 |
|---|---|
| 刷新补丁清单 → 门禁 → 提交 | `powershell -NoProfile -File ops\dev\after_edit.ps1 -Message "fix(xxx): ..."` |
| 只跑门禁 | `python ops\verify\release_gate.py` |
| 门禁（并发编辑时会自动重试并记 flaky） | 同上，看结尾 `[flaky]` 行 |
| 门禁 + 配置基准 | `$env:DEMO_BENCH='1'; python ops\verify\release_gate.py` |

门禁不过就不许提交（`after_edit.ps1` 会拦住）。

---

## 密钥

| 想做什么 | 跑什么 |
|---|---|
| 看当前 key 存在哪几处 | `python ops\dev\rotate_key.py --check` |
| 换 key（交互，不回显） | `python ops\dev\rotate_key.py --name deepseek` |
| 用配置里的 key 真调一次 | `python ops\dev\rotate_key.py --verify` |

**换 key 的正确顺序**：生成新的 → 换上 → 验证通过 → 再删旧的。别反过来。

---

## 评测（花小钱）

| 想做什么 | 跑什么 | 花费 |
|---|---|---|
| 配置基准（20 条，3 轮） | `python ops\eval\bench.py --cases holdout_v2_20261026.jsonl --runs 3` | ≈¥4.6 |
| **配置基准（45 条，噪声更小）** | `python ops\eval\bench.py --cases holdout_v3_20261027.jsonl --runs 3` | ≈¥10 |
| **人工校准判据** | `python ops\eval\calibrate.py`（逐条 y/n），然后 `--report` | 0 |
| 认真话题触发（离线 3 套） | `python ops\eval\check_serious_trigger.py` | 0 |
| 真实历史回放 | `python ops\eval\serious_backlog.py` | ≈¥0.05 |
| 影子样本报告 | `python ops\eval\serious_shadow_report.py` | 0 |
| 冻结集校验 | `python ops\eval\freeze_holdout.py --cases holdout_v2_20261026.jsonl --check` | 0 |

**规则：差异小于 `bench` 给出的"可分辨下限"（约 0.32），一律当噪声。**

---

## 定时任务一览

| 任务 | 频率 | 作用 |
|---|---|---|
| `DemoBotDaily` | 每天 09:00 | 每日体检（只读） |
| `DemoBotFullBackup` | 每 4 周周日 05:10 | 全量备份（可启动） |
| `DemoBotAlerts` | 原有 | 巡检 |

查状态：`Get-ScheduledTask -TaskName DemoBot* | Select TaskName,State`

---

## 启动顺序（重启后）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:/demo\boot_all.ps1"
```

它会：清掉普通 QQ → 起 NapCat → 等 6199 → 起 AstrBot（带 `DASHBOARD_PORT=22877`）→ 起管家。
**注意**：`NapCatWinBootMain` 注入完会退出，判断"好没好"只看端口 6199，不看进程。

---

## 出问题先看哪里

| 症状 | 先看 |
|---|---|
| 机器人不回话 | `daily.py` → 6199 是否 Listen；`astrbot_run2.log` 尾部 |
| 主动消息不发 | `Bot管家.log` 尾部（"刚聊过跳过"是正常） |
| 语音怪 | TTS 预处理：`ops/eval` 里没有，直接看 `core/provider/sources/minimax_tts_api_source.py` 的 `_preprocess_text` |
| 数字对不上 | `observe.py` 的 `sent_rate` 与 `payload_mismatch` |
| 配置像换了个人 | `daily.py` 的配置漂移项；`ops/CONVENTIONS.md` 的"单改权" |

---

## 三条纪律（都是踩过坑写下来的）

1. **配置一次只能一个人改**，改完跑 `bench.py --runs 3` 留指纹。
2. **单轮分数不能下结论**，波动本身就有 0.5。
3. **备份要能启动才算备份**——用 `--full`，并定期 `drill`。
