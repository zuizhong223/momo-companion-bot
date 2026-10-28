# 运维手册

> 这些命令来自真实运行环境，公开版把路径/任务名/QQ 号换成了占位符。部分内部告警、日报、私有 holdout 没有随仓库发布，命令里保留它们是为了说明流程。

---

## 1. 启动顺序

```powershell
# 1) NapCat（QQ 协议桥）
D:\napcat\NapCat\NapCat.52230.Shell\napcat.bat

# 2) AstrBot + butler（一条命令）
powershell -NoProfile -ExecutionPolicy Bypass -File D:\demo\scripts\boot_all.ps1
```

`boot_all.ps1` 做四件事：

1. 清掉普通 QQ 进程（否则会抢登录）；
2. 起 NapCat，等 6199 端口；
3. 起 AstrBot（`DASHBOARD_PORT=22877`），等 22877；
4. 起 `butler.py`，写 `boot_health.json`。

判断「好没好」只看端口，不看进程：`NapCatWinBootMain` 注入完会退出，这是正常的。

---

## 2. 日常检查

```powershell
# 端口 / QQ 连接 / 发送台账 / 日志错误数
python ops\tasks.py status

# 收件箱：发送成功率、失败原因、TTS、主动性
python ops\verify\observe.py

# 每日体检（只读；会写 ops\verify\daily_YYYYMMDD.md）
python ops\verify\daily.py

# watchdog 内部状态
python ops\deploy\watchdog.py --status
```

`daily.py` 的重点：

- 6199 / 22877 是否 Listen；
- 发送成功率、`unknown` 比例、`payload_mismatch`；
- 配置文件 sha 是否和基准一致；
- 生活层 / 情绪层开关状态；
- 最近 24h 是否有异常重试。

---

## 3. 重启

```powershell
python ops\tasks.py restart-astrbot   # 只重启 AstrBot（带互斥锁）
python ops\tasks.py restart-butler    # 只重启 butler
powershell -File scripts\boot_all.ps1 # 全套，幂等
```

不要直接 `kill` 后再手动起两个实例：`butler.py` 有 PID 单实例锁，第二个会退出；AstrBot 会撞 22877。

---

## 4. 发送台账

```sql
-- 各状态数量
select state, count(*) from yy_send_task group by state;

-- 最近失败
select id, state, retcode, error, created_at
from yy_send_task
where state in ('failed','unknown')
order by created_at desc limit 20;
```

状态含义：

| 状态 | 含义 | 处理 |
|---|---|---|
| `pending` | 待发送 | 正常 |
| `sending` | 已认领 | 若长时间不动，看是否进程崩了 |
| `sent` | 平台接受 | 正常 |
| `failed` | 明确失败 | 看 retcode；限流/权限/内容拒绝分开处理 |
| `unknown` | 平台可能已接受，但连接断了 | **不要盲目重试**，先对账 |
| `skipped` | 门禁/配额跳过 | 正常 |

`unknown` 是最危险的：重试会重复，不重试会丢。处理方式是看 `yy_send_attempt` 和平台回执，不自动重试。

---

## 5. 备份

```powershell
python ops\backup\backup.py backup          # 增量/整包
python ops\backup\backup.py backup --full   # 可启动的全量包
python ops\backup\backup.py verify          # 校验最新包
python ops\backup\backup.py drill           # 解包到临时目录，跑校验
python ops\backup\drill_full.py             # 独立目录冷启动
python ops\backup\prune.py --keep 30 --apply
```

备份里有什么：

- `core/data`（数据库、知识库）；
- `core/astrbot`（改过的文件）；
- `ops/`、`patches/`、表情库、根目录脚本；
- `manifest.json`（文件指纹）。

备份里没有什么：

- 框架代码 / `main.py` / venv；
- 密钥（`cmd_config.json` 的三处 key 写成 `REDACTED`）；
- 运行日志、缓存。

> **恢复后必须补钥匙**，否则「能启动但发不出消息」。见 `ops/backup/RESTORE.md`。

---

## 6. 恢复

```powershell
# 1) 装同版本 AstrBot，确认 core\main.py 存在
# 2) 解包到临时目录
Expand-Archive -Path D:\demo\backups\demo_backup_<ts>.zip -DestinationPath D:\restore_tmp -Force

# 3) 贴补丁
cd D:\demo
python ops\patches\patches.py apply --force

# 4) 覆盖数据（先停 AstrBot）
Copy-Item D:\restore_tmp\core\data\* `
          D:\demo\instances\<实例id>\core\data\ -Recurse -Force

# 5) 补 .env / deepseek_key.txt / minimax_key.txt
python ops\dev\rotate_key.py --check

# 6) 启动
$env:DASHBOARD_PORT='22877'; $env:PYTHONIOENCODING='utf-8'
```

验收标准：

1. 22877 / 6199 Listen；
2. 日志出现「适配器已连接」；
3. `backup.py drill` 的 DB/配置/表计数/知识库指纹一致；
4. `observe.py` 的 `sent` 与备份时一致；
5. 真发一条，`state=sent` 且历史 +1；
6. 真收一条，被动回复正常。

1–4 可离线验，5–6 必须联通 QQ。

---

## 7. 发布门禁

```powershell
# 全量门禁
python ops\verify\release_gate.py

# 门禁 + 配置基准（会花钱）
$env:DEMO_BENCH='1'; python ops\verify\release_gate.py

# 改完核心文件：刷新补丁清单 + 门禁 + 提交
powershell -NoProfile -File ops\dev\after_edit.ps1 -Message "fix(xxx): ..."
```

门禁覆盖：

- 单测（约 78 项离线检查）；
- Python 编译；
- 补丁清单 sha256；
- 关键接线检查（防止「测试全绿但功能被覆盖」）；
- 备份 verify；
- 配置指纹 / 单改权。

门禁失败时 `after_edit.ps1` 不会提交。

---

## 8. 配置单改权

```powershell
python ops\dev\persona_lock.py acquire --who <name> --why "改什么"
# 改 persona.txt
python ops\eval\bench.py --cases <holdout> --runs 3
python ops\dev\persona_lock.py release
```

- 锁被占时 `acquire` 拒绝；
- 超过 4 小时算过期，可 `--force`；
- `release` 把 `sha_before / sha_after / 是否改动` 写进 `ops/persona_history.jsonl`；
- `status` 会提示「配置和基准不一致 = 改过但没跑 bench」。

锁是约定，不是强制；两个 agent 都认它才有用。

---

## 9. Docker（本机不迁移，仅快照）

```powershell
python ops\deploy\make_build_ctx.py   # 组 26MB 精简上下文
cd D:\demo-build\build
docker build -t demo:4.29.1 .
```

容器自检：

```powershell
python ops\docker\preflight.py
python ops\tasks.py docker-check
```

容器里没有 Windows 根文件，`DEMO_CONTAINER=1` 会跳过相关检查。

---

## 10. 出问题按这个顺序查

1. `python ops\tasks.py status` —— 端口 / QQ / ERRO；
2. `Get-Content astrbot_run2.log -Tail 50` —— 最近的 WARN/ERRO；
3. `python ops\deploy\watchdog.py --status` —— 是不是掉登录（`qq_conn=0`）；
4. 台账异常：`select state,count(*) from yy_send_task group by state`；
5. 备份异常：`python ops\backup\backup.py verify`；
6. 补丁不同步：`python ops\patches\patches.py verify` → `export`。

常见症状：

| 症状 | 先看 |
|---|---|
| 不回话 | 6199 是否 Listen；AstrBot 日志尾部 |
| 主动消息不发 | `butler.log` 尾部（「刚聊过跳过」是正常） |
| 语音怪 | `minimax_tts_api_source.py` 的 `_preprocess_text` |
| 数字对不上 | `observe.py` 的 `sent_rate` / `payload_mismatch` |
| 配置像换了个人 | `daily.py` 的配置漂移项；`persona_lock.py status` |

---

## 11. 几条硬规矩

- `.cmd` 存 GBK；`.ps1` 要么 UTF-8 带 BOM，要么纯 ASCII；
- 别在仓库根 `docker build`（上下文太大），先 `make_build_ctx.py`；
- 别并发重启 AstrBot（会撞 22877），用 `tasks.py restart-astrbot`；
- 测试复制数据库用 `_dbcopy.snapshot()`，别用 `shutil.copy2`（WAL 会拿到不一致快照）；
- 备份里的密钥是脱敏的，恢复后要补钥匙；
- `unknown` 状态不要自动重试。
