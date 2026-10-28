# QQ Bot Ops · 脱敏工程样例

> 一个跑在本机的 QQ 机器人控制面：主动消息、发送台账、出站门禁、生活状态、结构化记忆、风格检索，以及一套可评测、可回滚、可恢复的运维流程。
>
> **这是脱敏工程样例。** 仓库不包含真实配置、聊天记录、用户资料、训练数据、音色、模型、截图、数据库或密钥；所有示例都是合成的。

---

## 1. 项目是什么

这不是一个「接上大模型就会聊天」的 demo，而是一个**长期在线的机器人控制面**：

- 宿主是本地 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 4.29.1 + NapCat（QQ 协议桥）；
- 仓库里有 **1,500 行的主动消息管家**、**33 个改过的 AstrBot 核心文件**、**200+ 个运维/评测模块**、**98 个测试文件**；
- 目标不是「回复得像某个角色」，而是「消息发得出去、记得住、不重复、不崩、不改坏、能回滚」。

核心组成：

| 层 | 位置 | 作用 |
|---|---|---|
| 主动消息管家 | `butler.py` | 09:00–23:00 按随机间隔主动发消息；文字/语音/图文比例控制；视频分享；截图；PID 单实例；失败回退 |
| AstrBot 补丁 | `patches/files/astrbot/` | 发送台账、出站门禁、生活层、结构化记忆、情绪状态、风格检索、认真话题探针、历史清洗、TTS 口语化等 |
| 生活层 | `ops/life/` | 作息/上课/练习事件模型，UTC 存储，软遗忘，24h 模板，影子模式 |
| 记忆层 | `ops/memory/`、`patches/.../memory_facts.py` | 事实抽取、候选/确认/撤回、supersede、召回 |
| 评测层 | `ops/eval/` | 冻结 holdout、3 轮基准、AI 裁判、人工校准、相似度指标、噪声下限 |
| 可靠性 | `ops/verify/`、`ops/backup/`、`ops/deploy/` | 发布门禁、每日体检、watchdog、备份/校验/恢复演练、Docker 自检 |
| 安全 | `ops/security/` | 密钥清单与轮换、日志敏感信息扫描、备份脱敏、Git 历史残留检查 |

---

## 2. 运行时架构

```
QQ 手机端 / 群
      │
      ▼
NapCat（OneBot v11，反向 WS 6199）
      │
      ▼
AstrBot 4.29.1（本机，Dashboard 22877）
  ├─ 入站：消息合并 → 历史清洗/排除 → 记忆捕获 → 生活/情绪注入 → Agent
  ├─ 出站：出站门禁（长度/括号/口癖/贴纸配额）→ TTS → 发送台账 → QQ
  └─ 主动：proactive_service ← butler.py（定时/随机触发、同源生成）
      │
      ▼
DeepSeek / OpenAI 兼容 provider、MiniMax TTS/Embedding、本地 Whisper、Edge TTS
```

设计上把「**生成**」和「**投递**」拆开：

- 生成只负责内容，必须复用同一份配置、同一份历史、同一个 provider；
- 投递由发送台账（`yy_send_task`）管状态：`pending → sending → sent / failed / unknown`；
- 所有主动消息都先落盘再发送，重启后能续跑，不会因为进程崩溃重复轰炸；
- 出站门禁不通过就不发，宁可少发，不发错。

---

## 3. 工程上值得看的地方

### 3.1 发送台账：把「发消息」当成状态机

`patches/files/astrbot/core/utils/send_ledger.py`（约 1,000 行）处理：

- 并发认领：两个线程同时抢同一条任务，只有一个能赢；
- 平台已接受但连接断开 → `unknown`，不盲目重试；
- 多段回复：`reply_id` 相同、`seq` 递增；
- 超时/明确拒绝/限流的不同重试策略；
- 与历史写入绑定：消息真发出去了才写 assistant 轮次，避免「机器人以为自己说过」。

对应测试：`ops/tests/test_ledger.py`、`test_p0_*`、`test_recovery_queue.py`。

### 3.2 出站门禁：风格不是 prompt 里写一句就行

`outbound_gate.py` 在发送前做确定性检查：

- 长度：不同场景有不同上限；
- 括号：按最近 N 条做预算，避免每条都「（」；
- 口癖：`好好好` / `哈哈哈哈` / `捏` 等有频率上限；
- 贴纸：按天配额，避免刷屏；
- 重复：短时间同句/近似句降权。

### 3.3 生活层：只推进状态，不调用模型

`patches/.../life_state.py` 把作息/上课/游戏/练习建模成事件：

- 稳定 `event_id`，重启不重复生成；
- `planned → active → done/missed`，软遗忘，可恢复；
- 只给生成层「可以说什么」，不直接发消息；
- 影子库模式：先只读对比，确认不会污染正式库。

### 3.4 记忆层：事实和事件分开

- `yy_fact2`：稳定事实（养宠物、换过东西、对花粉过敏等合成示例）；
- `yy_event_memory`：有时间感的事（今天考试、昨天冲突、约好周末）；
- 候选 → 确认 → 撤回/supersede；不把模型推断直接写成事实。

### 3.5 发布门禁

`ops/verify/release_gate.py` 跑测试 + 静态检查 + 补丁清单校验；`ops/backup/` 做备份、校验、恢复演练；`ops/security/` 做密钥/日志残留扫描。

---

## 4. 快速开始

```powershell
git clone https://github.com/zuizhong223/qq-bot-ops-sample.git D:\demo
cd D:\demo
Copy-Item .env.example .env
# 填写 provider / dashboard / openapi 配置
python -m pip install -r requirements.lock
python -m py_compile butler.py
```

更完整的部署步骤见 [docs/DEPLOY.md](docs/DEPLOY.md)。

---

## 5. 目录

```
butler.py                 主动消息管家
patches/files/astrbot/    改过的 AstrBot 核心文件（只含代码）
ops/life/                 生活层
ops/memory/               结构化记忆
ops/eval/                 评测/盲测/相似度
ops/verify/               发布门禁/每日体检
ops/backup/               备份/恢复演练
ops/deploy/               watchdog/部署
ops/security/             密钥与日志扫描
docs/                     架构/部署/评测/运维/安全
examples/                 仅说明，不含配置或真实对话
```

---

## 6. 脱敏说明

- 示例对话记录、群聊、记忆、配置、训练数据、音色、模型、截图、数据库、日志、备份、密钥 **全部不发布**；
- 代码中的个人标识、路径、QQ/群号、实例 UUID、音色 ID 使用占位符；
- 测试/示例中的生活事实是合成的，不对应任何真实用户；
- 详见 [docs/SANITIZATION.md](docs/SANITIZATION.md) 和 [docs/CHANGELOG_20261028.md](docs/CHANGELOG_20261028.md)。

---

## 7. 许可

GPL-3.0；AstrBot 上游代码的许可证见 `NOTICE`。
