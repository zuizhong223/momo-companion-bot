# 部署

> 这是「怎么从零搭起来」的说明。公开版没有完整 AstrBot 源码、没有真实配置、没有密钥；你需要自己装宿主、自己填 key。
>
> 下面的路径都是占位符：`D:\demo`、`<ASTRBOT_INSTANCE>`、`100000000`。

---

## 0. 你需要什么

- Windows 10/11（脚本按 Windows 写；Linux 需要改路径/服务）
- Python 3.12
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) **4.29.1**（补丁按这个版本改的）
- NapCat（QQ 协议桥，OneBot v11）
- 一个 QQ 小号（不要用主号）
- DeepSeek / OpenAI 兼容 provider key
- 可选：MiniMax TTS/Embedding key、本地 Whisper、Edge TTS

---

## 1. 装 AstrBot

按官方文档装 4.29.1，得到一个实例目录：

```
<ASTRBOT_INSTANCE>/
├── core/
│   ├── main.py
│   ├── astrbot/
│   └── data/
└── venv/
```

确认：

```powershell
cd <ASTRBOT_INSTANCE>\core
python main.py
```

能启动、能打开 Dashboard（默认 6185 或你配置的端口）即可先停掉。

---

## 2. 贴补丁

**先备份** `core/astrbot/`：

```powershell
Copy-Item <ASTRBOT_INSTANCE>\core\astrbot `
          <ASTRBOT_INSTANCE>\core\astrbot.bak -Recurse
```

把本仓库的补丁文件覆盖进去：

```powershell
# 在仓库根执行
Copy-Item patches\files\astrbot\* `
          <ASTRBOT_INSTANCE>\core\astrbot\ -Recurse -Force
```

覆盖后在实例里校验：

```powershell
python ops\patches\patches.py verify
```

> 补丁是「改过的文件副本」，不是 diff；升级 AstrBot 后需要重新导出/合并。

---

## 3. 建 venv / 装依赖

```powershell
cd <ASTRBOT_INSTANCE>
python -m venv venv
.\venv\Scripts\python.exe -m pip install -U pip
.\venv\Scripts\python.exe -m pip install -r D:\demo\requirements.lock
```

Linux：

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements.lock.linux
```

依赖锁是原始环境 181 个包的实际版本；如果平台不同，可能需要放宽版本。

---

## 4. 配置

### 4.1 `.env`

```powershell
Copy-Item D:\demo\.env.example D:\demo\.env
notepad D:\demo\.env
```

填：

```ini
DEEPSEEK_KEY=sk-...
DASHBOARD_PASSWORD=...
OPENAPI_KEY=...
MINIMAX_KEY_FILE=D:\demo\minimax_key.txt
```

`.env` 不要提交。

### 4.2 Dashboard / Provider

打开 AstrBot Dashboard：

- 配置 `DASHBOARD_PORT=22877`；
- 配 DeepSeek / OpenAI 兼容 provider；
- 配 TTS（MiniMax 或 Edge TTS）；
- 配 STT（可选，本地 Whisper）；
- 把 `examples/persona.sample.md` 的内容放进配置，或让 butler 读取 `persona.txt`。

### 4.3 NapCat

- 启动 NapCat，登录 QQ 小号；
- 配 OneBot v11 反向 WS：`ws://127.0.0.1:6199/ws`；
- 确认 AstrBot 日志出现「aiocqhttp(OneBot v11) 适配器已连接」。

---

## 5. 启动

```powershell
# 一条命令：NapCat → AstrBot → butler
powershell -NoProfile -ExecutionPolicy Bypass -File D:\demo\scripts\boot_all.ps1
```

或者分开：

```powershell
# AstrBot
$env:DASHBOARD_PORT='22877'; $env:PYTHONIOENCODING='utf-8'
cd <ASTRBOT_INSTANCE>\core
..\venv\Scripts\python.exe -u main.py

# butler
cd D:\demo
<ASTRBOT_INSTANCE>\venv\Scripts\python.exe -u butler.py
```

健康检查：

```powershell
python ops\tasks.py status
```

期望：

- 22877 open；
- 6199 open；
- 日志无连续 ERROR；
- `butler.pid` 存在且只有一个进程。

---

## 6. 首次验证

1. 私聊发一句「在吗」，应收到回复；
2. 发语音，确认 STT 能转文字；
3. 让 butler 主动发一条（或把间隔调短），确认能发出；
4. 看台账：

```sql
select state, count(*) from yy_send_task group by state;
```

5. 跑门禁：

```powershell
python ops\verify\release_gate.py
```

6. 跑一次备份演练：

```powershell
python ops\backup\backup.py backup --full
python ops\backup\backup.py verify
python ops\backup\backup.py drill
```

---

## 7. 配置项

`butler.py` 支持这些环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEMO_ROOT` | `D:\demo` | 项目根 |
| `DEMO_ASTRBOT_BASE` | `http://127.0.0.1:22877` | AstrBot 地址 |
| `DEMO_UMO` | `aiocqhttp:FriendMessage:100000000` | 默认会话 |
| `DEMO_DB` | `<ROOT>/instances/<uuid>/core/data/data_v4.db` | 数据库 |
| `DEMO_MINIMAX_KEY_FILE` | `<ROOT>/minimax_key.txt` | TTS key 文件 |
| `DEMO_SCREEN_DIR` | `<ROOT>/screens` | 截图目录 |
| `DEMO_MAX_PROACTIVE_PER_DAY` | `8` | 每日主动上限 |

生活/评测相关：

| 变量 | 说明 |
|---|---|
| `DEMO_LIFE` | 生活层总开关 |
| `DEMO_LIFE_SHADOW` | 只记录不注入 |
| `DEMO_LIFE_REMINDER` | 提醒注入 |
| `DEMO_BENCH` | 门禁时跑配置基准 |
| `DEMO_CONTAINER` | 容器内跳过 Windows 检查 |

---

## 8. 常见问题

| 问题 | 原因 | 处理 |
|---|---|---|
| AstrBot 起不来 | 端口被占 / venv 不对 | 看 `astrbot_run2.log`；`tasks.py status` |
| QQ 连不上 | NapCat 没登录 / token 错 | 重新扫码；看 6199 |
| 能收不能发 | 发送台账/权限/风控 | 看 `yy_send_task` 的 retcode |
| 语音怪 | TTS 预处理/音色 | 看 `minimax_tts_api_source.py` 的 `_preprocess_text` |
| 主动消息不发 | 配额/间隔/刚聊过/禁用开关 | 看 `butler.log` |
| 配置不对 | 读错 persona 文件 | `python ops\dev\persona_lock.py status` |
| 备份恢复后发不出 | 没补 key | `python ops\dev\rotate_key.py --check` |

---

## 9. 升级 AstrBot

1. 备份 `core/`；
2. 装新版本；
3. 重新贴补丁（可能有冲突）；
4. `python ops\patches\patches.py verify`；
5. `python ops\verify\release_gate.py`；
6. 看 `ops/CONVENTIONS.md` 的接线检查，不要只看单测绿。

---

## 10. 不要做的事

- 不要把 `.env`、数据库、截图、备份提交到公开仓库；
- 不要把 22877 / 6199 直接暴露公网；
- 不要用主号当机器人；
- 不要在没有授权的情况下克隆别人的声音/聊天风格；
- 不要在公开仓库里放真实配置/聊天记录。
