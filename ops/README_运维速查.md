# 运维命令速查（2026-10-27）

> 所有命令在项目根 `D:/demo` 下、用实例 venv 跑。
> 下面用 `py` 代替 `<venv>\Scripts\python.exe`；实际可写成
> `.\instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe`。

---

## 一、看现在什么状态

```powershell
py ops\tasks.py status          # 端口 / QQ 连接 / 台账 / 历史 / ERRO 行数
py ops\alerts\alerts.py         # 24h：请求数、失败率、TTS、主动性、QQ、备份
py ops\alerts\daily.py --no-file   # 一天的总览（不写文件）
py ops\deploy\watchdog.py --status # watchdog 内部状态（含 qq_conn / 连续掉线次数）
```

## 二、重启

```powershell
py ops\tasks.py restart-astrbot # 只重启 AstrBot（带互斥锁，不会并发撞端口）
py ops\tasks.py restart-butler  # 只重启管家
powershell -File boot_all.ps1   # 全套（NapCat → AstrBot → 管家），幂等
```

> NapCat / QQ 不要随便重启：需要重新扫码。**掉登录时重启也没用，只能扫码。**

## 三、测试与门禁

```powershell
py ops\tasks.py test              # 8 套核心测试
py ops\tasks.py test --repeat 3   # 连跑 3 遍，验证稳定性
py ops\verify\release_gate.py     # 全量门禁（含另一条线的 90+ 项）
py ops\patches\patches.py verify  # 补丁包与实例是否一致
```

## 四、备份

```powershell
py ops\backup\backup.py backup    # 打整包（自动脱敏：密钥不进包）
py ops\backup\backup.py check     # 必备文件 + DB integrity + KB 一致性
py ops\backup\backup.py verify    # 校验最新整包
py ops\backup\backup.py drill     # 演练：解包→校验→检索基线
py ops\backup\drill_full.py       # 完整演练：独立目录跑起来
py ops\backup\backup.py restore --zip <包> --to <目录>
```

> **恢复后必须补密钥**（`.env` / `deepseek_key.txt` / `minimax_key.txt` +
> `cmd_config.json` 里 3 处 `REDACTED`），自检 `py ops\dev\rotate_key.py --check`。
> 详见 `ops\backup\RESTORE.md`。

## 五、补丁包

```powershell
py ops\protect\snapshot.py --manifest-only  # 重生成清单（并集，只增不减）
py ops\patches\patches.py export            # 把实例里改过的文件复制进包
py ops\patches\patches.py apply             # 升级 AstrBot 后贴回
py ops\patches\patches.py verify
```

## 六、依赖与部署

```powershell
py ops\deploy\check_lock.py     # 锁 vs 实际安装，有差异退出码 1
py ops\deploy\regen_lock.py     # 重新生成 Windows + Linux 两份锁
powershell -File ops\deploy\install.ps1   # 新机器：建 venv + 按锁安装
```

## 七、Docker（本机不迁移，仅当快照）

```powershell
py ops\deploy\make_build_ctx.py            # 组 26MB 精简上下文
cd 'D:/demo\build'; docker build -t demo:4.29.1 .
py ops\tasks.py docker-check               # 容器里跑 backup check
docker run --rm -v <备份>:/app/backups:ro -v <数据副本>:/app/core/data `
  -e DEMO_ROOT=/app -e DEMO_CORE=/app/core -e DEMO_BACKUPS=/app/backups `
  -e DEMO_RESTORE=/tmp/restore_test demo:4.29.1 python /app/ops/backup/drill_full.py
```

容器相关环境变量：`DEMO_ROOT` / `DEMO_CORE` / `DEMO_BACKUPS` /
`DEMO_RESTORE` / `DEMO_PY` / `DEMO_CONTAINER=1`（跳过 Windows 根文件检查）。

## 八、计划任务（4 个）

| 任务 | 频率 | 作用 |
|---|---|---|
| `DemoBotWatchdog` | 5 分钟 | 端口 + **QQ 连线**；连续 2 次 down 才重启（掉登录只告警不重启） |
| `DemoBotAlerts` | 每小时 | 巡检，超阈值写告警 |
| `DemoBotDaily` | 每天 09:00 | 日报 → `ops\alerts\日报_YYYYMMDD.md` |
| `DemoBotFullBackup` | 每 4 周 | 全量备份 |

```powershell
Get-ScheduledTask -TaskName 'DemoBot*' | ForEach-Object {
  $i = Get-ScheduledTaskInfo -TaskName $_.TaskName
  "$($_.TaskName) $($_.State) 上次=$($i.LastRunTime) 结果=$($i.LastTaskResult)" }
```

## 九、出问题时按这个顺序查

1. `py ops\tasks.py status` —— 端口 / QQ / ERRO
2. `Get-Content astrbot_run2.log -Tail 50` —— 最近的 WARN/ERRO
3. `py ops\deploy\watchdog.py --status` —— 是不是掉登录（`qq_conn=0`）
4. 台账异常：`select state,count(*) from yy_send_task group by state`
5. 备份异常：`py ops\backup\backup.py verify`
6. 补丁不同步：`py ops\patches\patches.py verify` → `export`

## 十、几条硬规矩（踩过坑）

- `.cmd` 必须存成 **GBK**；`.ps1` 要么 **UTF-8 带 BOM**、要么**纯 ASCII**（`boot_all.ps1` 就是纯 ASCII）
- **别在仓库根 `docker build`**（15GB 上下文），先 `make_build_ctx.py`
- **别并发重启** AstrBot（会撞 22877），用 `tasks.py restart-astrbot`
- 测试里复制数据库用 `ops\tests\_dbcopy.snapshot()`，别用 `shutil.copy2`（WAL 会拿到不一致快照）
- 备份里的密钥是脱敏的，**恢复后要补钥匙**，否则"能启动但发不出消息"

## 十一、重启与自检（2026-10-27 新增）

`powershell
py ops\deploy\safe_restart.py --check   # 只编译自检（0.6 秒）；出错退出码 2
py ops\deploy\safe_restart.py           # 自检 → 重启 → 验证端口和 QQ
`

> 并行有人改代码时，**先 --check 再重启**。今晚就出过一次中间态语法错误（坏文件持续几分钟）。

## 十二、watchdog 现在看什么（每 5 分钟）

| 检查 | 判定 |
|---|---|
| 端口 22877 / 6199 | 连续 2 次 down → 跑 boot_all + 通知 |
| QQ 连线（6199 established） | 连续 2 次 0 → WARN + 通知（重启没用，要扫码） |
| 管家进程 | 连续 2 次不在 → 自动拉起 + 通知 |
| **日志新增 ERRO** | 5 分钟内 ≥3 条 → WARN + 通知，附最后一条 |

状态里能看到：qq_conn / qq_down_streak / butler_alive / butler_down_streak / erro_total / log_size。

## 十三、启动记录

oot_all.ps1 每次启动往 ops\boot_manifest.txt 追加一行：

`
2026-10-27 23:16:55 manifest=3D1C8DBC91A50D0E files=33
`

manifest = patches\MANIFEST.txt 的 SHA256 前 16 位。想知道跑的是哪一版就看它。
