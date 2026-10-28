# 恢复手册（RESTORE）

> 2026-10-27 演练结论：**备份不是"解压即启动"的整机镜像。**
> 包里只有 `core/data`（库与知识库）、`core/astrbot`（22 个改过的文件）、`ops/`、`patches/`、`表情库/` 和根目录脚本。
> **`main.py` 与 AstrBot 框架代码不在包里**，所以恢复 = 装好同版本 AstrBot，再把包里的东西贴回去。

---

## 0. 包里有 / 没有

| 有 | 没有 |
|---|---|
| `core/data/data_v4.db`（会话、配置、台账） | `main.py`、框架代码、`venv` |
| `core/data/knowledge_base/`（doc.db + faiss） | 任何密钥（**脱敏设计**） |
| `core/astrbot/` 22 个改过的文件 | 运行日志、缓存 |
| `ops/`、`patches/`、`表情库/`、根目录脚本 | — |
| `manifest.json`（含各文件指纹） | — |

---

## 1. 恢复步骤（按顺序）

```powershell
# ① 装同版本 AstrBot（4.29.1），得到实例目录
#    D:/demo\instances\<实例id>\core\main.py 必须存在

# ② 解包到临时目录
$z = "D:/demo\backups\demo_backup_<时间戳>.zip"
Expand-Archive -Path $z -DestinationPath D:/demo/restore_tmp -Force

# ③ 先贴补丁文件（补丁包自带哈希校验）
cd "D:/demo"
python ops\patches\patches.py apply --force

# ④ 覆盖数据（覆盖前先停 AstrBot）
Copy-Item D:/demo/restore_tmp\core\data\* `
          "D:/demo\instances\<实例id>\core\data\" -Recurse -Force

# ⑤ 补钥匙（少了这一步，启动后一句话也发不出）
#    .env / deepseek_key.txt / minimax_key.txt 三份文件
#    以及 cmd_config.json 里被写成 REDACTED 的三处：
#      provider_sources[deepseek_source].key
#      provider[minimax_qu].api_key
#      provider[minimax_emb].embedding_api_key
python ops\dev\rotate_key.py --check      # 看还缺哪几处

# ⑥ 启动（必须带 DASHBOARD_PORT）
$env:DASHBOARD_PORT='22877'; $env:PYTHONIOENCODING='utf-8'
$env:PATH='D:/demo\ffmpeg;'+$env:PATH
Start-Process "D:/demo\instances\<实例id>\venv\Scripts\python.exe" `
  -ArgumentList '-u','main.py' `
  -WorkingDirectory "D:/demo\instances\<实例id>\core" -WindowStyle Hidden
```

---

## 2. 验收到什么程度才算恢复成功

| # | 检查 | 命令 / 看什么 |
|---|---|---|
| 1 | 端口 | 22877 Listen |
| 2 | 适配器 | 日志有 `适配器已连接` |
| 3 | 数据一致 | `python ops\backup\backup.py drill` |
| 4 | 台账 | `python ops\verify\observe.py`，`sent` 与备份时一致 |
| 5 | 能发出去 | 发一条真消息，看 `state=sent` 且历史 +1 |
| 6 | 能收进来 | 手机上发一句，看被动回复 |

**1~4 可以离线验，5~6 必须联通 QQ。**

---

## 3. 2026-10-27 演练记录

| 步骤 | 结果 |
|---|---|
| `backup.py drill`（最新包） | ✅ 数据库完整性、配置哈希、四个表计数、知识库指纹、检索基线全一致 |
| 解压到临时目录 | ✅ 37.1 MB → `D:/demo/restore_rehearsal` |
| 检查缺失项 | 缺 `.env` / `deepseek_key.txt` / `minimax_key.txt`；`cmd_config` 3 处 `REDACTED`（**符合脱敏设计**） |
| 补钥匙 + 禁用平台 | ✅ 成功 |
| **冷启动** | ❌ **失败：`core\main.py` 不存在** —— 包里没有框架代码 |

**结论：`drill` 只证明"数据能还原且一致"，不证明"能启动"。** 真正恢复必须按上面第 1 节做。

---

## 4. 缺口与建议

| 缺口 | 影响 | 建议 |
|---|---|---|
| 包内无框架代码 | **已解决**：`backup --full` 会打进 `core/` 整棵树（实测 +7.7 MB，因缓存与 data 被排除） |
| 无自动"补钥匙"步骤 | 恢复后静默无响应 | 已写进本文档第 ①⑤ 步；`rotate_key.py --check` 可自检 |
| 演练未联通 QQ | 收发链路没验 | 恢复后按第 2 节第 5、6 项人工验一次 |

---

## 5. 一句话

**备份保住了"机器人的记忆"，但没保住"机器人的身体"。**
身体是 AstrBot 那套代码——它得另外存一份。

---

## 6. 更新（2026-10-27 06:23）：新增 `--full`，备份现在可以直接启动

```powershell
python ops\backup\backup.py backup --full      # 39MB/878 文件 -> 46.7MB/2713 文件
```

`--full` 会把 `core/` 整棵树（除 `data/` 与缓存）打进包，**包含 `main.py` 和框架代码**。

### 实测记录（本轮真跑过）

| 步骤 | 结果 |
|---|---|
| `backup --full` | OK 46.7 MB / 2713 文件 / verify PASS |
| 解压到 `D:/demo/restore_full` | OK `core/main.py` 存在 |
| 补钥匙 + 禁用平台 | OK |
| **冷启动（端口 22879）** | OK **20 秒起来，日志有 `AstrBot started`** |
| 数据核对 | OK integrity ok、personas 2、yy_send_task 191、会话 268 |
| 清理 | OK 进程与目录都已删除 |

**结论：用 `--full` 的包，恢复流程简化为「解压 → 补钥匙 → 启动」。**

### 仍然要注意

- 仍要补钥匙（脱敏不变）
- `venv` 不在包里：恢复机需要同版本 Python 环境
- 冷启动演练必须把平台 `enable=false`，否则会和线上抢 6199

---

## 7. 灾难恢复时要手填的东西（不进包）

覆盖审计见 `ops/backup/COVERAGE.md`。以下三类**故意不入包**，恢复时必须手动补：

### ① 密钥（3 处）

`.env`、`deepseek_key.txt`、`minimax_key.txt`，以及 `cmd_config.json` 里被写成 `REDACTED` 的三处
（`provider_sources[deepseek_source].key`、`provider[minimax_qu].api_key`、`provider[minimax_emb].embedding_api_key`）。

自检：`python ops\dev\rotate_key.py --check`

### ② NapCat 配置（4 个 json）

目录：`D:/demo/napcat\NapCat\NapCat.52230.Shell\versions\9.9.33-52230\resources\app\napcat\config\`

| 文件 | 内容 |
|---|---|
| `onebot11_<QQ>.json` | OneBot 端口 6199、token、上报地址 |
| `napcat_<QQ>.json` | QQ 号与快速登录 |
| `napcat_protocol_<QQ>.json` | 协议端配置 |
| `napcat.json` | 全局 |

**大小只有几 KB，但没有它们就要重新配 token 和端口。**

### ③ 音色权重（498 MB）

`GPT-SoVITS\GPT_SoVITS\pretrained_models\qu_s1_e15.ckpt`（296 MB）+
`qu_s2G_e8.pth`（202 MB）。

**唯一副本、不可再生。** 目前未纳入备份（用户决定原样保留、不额外打包）。
如果要让它可恢复，最小做法：把这两个文件单独压缩存档，和备份放在不同磁盘。
