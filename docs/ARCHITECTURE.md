# 架构

> 目标读者：想快速判断「这东西是怎么搭起来的、哪里容易坏、作者有没有想过」的人。
>
> 这个仓库是脱敏公开版：所有名字/QQ/路径/密钥都是占位符。

---

## 1. 运行时拓扑

```
┌──────────────┐   反向 WS (OneBot v11)    ┌──────────────────────────┐
│  QQ 客户端    │ ────────────────────────▶ │ NapCat（协议桥，6199）     │
└──────────────┘                           └────────────┬─────────────┘
                                                       │
                                                       ▼
                                          ┌──────────────────────────┐
                                          │ AstrBot 4.29.1（22877）   │
                                          │ 入站管线 / Provider / DB  │
                                          └──────┬──────────────┬────┘
                                                 │              │
                                   ┌─────────────▼───┐   ┌──────▼─────────┐
                                   │ butler.py        │   │ Dashboard/API  │
                                   │ 主动消息引擎      │   │ OpenAPI        │
                                   └─────────────┬───┘   └──────┬─────────┘
                                                 │              │
                                                 ▼              ▼
                                          ┌──────────────────────────┐
                                          │ SQLite data_v4.db        │
                                          │ yy_send_task / yy_fact / │
                                          │ yy_life_* / yy_history_* │
                                          └──────────────────────────┘
```

- **NapCat** 只负责 QQ 协议；它不保存业务状态。
- **AstrBot** 是宿主：消息事件、provider、历史、Dashboard 都在它里面。
- **butler.py** 是控制面：主动消息、B 站视频、截图、TTS 触发。
- **SQLite** 是唯一事实源：发送台账、记忆、生活、情绪、历史排除都落库。

---

## 2. 入站消息路径

```
QQ 消息
  → aiocqhttp_message_event.py
      ├─ 群聊关键词/At 判定
      ├─ 多段消息合并（message_merge.py）
      ├─ 历史清洗（history_sanitize.py）
      ├─ 历史排除（history_exclude.py）
      ├─ 事件记忆（event_memory.py）
      ├─ 结构化记忆捕获（memory_facts.py）
      ├─ 生活状态注入（life_state.py）
      ├─ 情绪状态注入（mood_state.py）
      ├─ 认真话题探针（serious_probe.py）
      └─ Agent（AstrBot 原生）
  → 回复文本
  → 出站门禁（outbound_gate.py）
  → TTS（MiniMax / Edge TTS）
  → 发送台账（send_ledger.py）
  → QQ
```

关键原则：

1. **注入是只读的**：生活/情绪/记忆注入只拼上下文，不在这里写库。
2. **捕获是旁路的**：记忆捕获失败不能影响回复；有超时和异常兜底。
3. **门禁在发送前**：风格规则不依赖模型自觉，发送前做确定性检查。
4. **历史在发送后**：只有真发出去的消息才写 assistant 轮次，避免「机器人以为自己说过」。

---

## 3. 主动消息路径

```
butler.py 定时器（09:00–23:00，40–140 分钟随机）
  → 判断：最近是否刚聊过 / 今日配额 / 禁用开关 / 单实例锁
  → 选角度：日常、吐槽、游戏、琴、天气、反问…
  → 调 AstrBot /api/v1/proactive/generate（同源配置 + 同源历史 + 同 provider）
  → 生成结果只写 assistant 草稿（不新增 user 轮次）
  → /api/v1/proactive/commit 落库
  → 发送（文字/语音/图文）
  → 失败回退纯文字；仍失败记 failed，不重试轰炸
```

为什么不用 butler 自己直连 DeepSeek：

- 会得到**第二份配置**和**第二份历史**，两边说话会漂；
- 主动消息写进历史后，用户回复时模型必须「记得自己说过」；
- provider 切换、限流、重试策略只需要在 AstrBot 里维护一份。

`ops/eval` 里有针对「同源」的接线测试（`test_agent_wiring.py`、`test_source_chain.py`）。

---

## 4. 数据模型（SQLite）

| 表 | 作用 | 关键约束 |
|---|---|---|
| `yy_send_task` | 发送台账：一条待发消息一行 | 状态机、`reply_id + seq` 唯一、幂等键 |
| `yy_send_attempt` | 每次投递尝试 | 记录 retcode/异常，便于区分「平台拒绝」和「网络超时」 |
| `yy_send_conflict` | 并发认领冲突 | 用于观察两个进程抢同一条 |
| `yy_history_commit` | 历史提交记录 | 与 `yy_send_task` 绑定，防重复写 |
| `yy_history_exclude` | 不进历史的来源 | 删除/撤回/隐私消息 |
| `yy_turn` | 轮次清单 | `start_turn/prepare_turn_commit` 两阶段 |
| `yy_recent_sent` | 最近发送缓存 | 去重、防复读 |
| `yy_fact` | 结构化事实 | `active/candidate/superseded/retracted`，单值唯一 |
| `yy_event_memory` | 有时间感的事件 | 今天/昨天/上周，带 TTL |
| `yy_mood` / `yy_mood_event` | 情绪状态与事件 | 衰减、事件驱动 |
| `yy_life_event` | 生活事件 | `planned/active/done/missed/forgotten` |
| `yy_life_state` | 生活层世界状态 | 版本号、时区、开关 |
| `yy_life_commitment` | 承诺 | 必须绑定 `activation_task_id` 才能 active |
| `yy_life_place` | 地点 | 只接受 `user_statement/schedule`，置信度 ≥0.7 |
| `yy_life_delay` | 延迟回复 | 睡眠窗口/普通窗口 |
| `yy_life_conflict` | 版本冲突 | 乐观锁失败留痕 |

时间一律 **UTC 存储**，展示时换角色时区（`Asia/Shanghai`）。夏令时不存在的本地时刻返回 `nonexistent`，重复时刻返回 `ambiguous`，调用方必须显式处理。

---

## 5. 模块地图

### 5.1 butler.py

| 区块 | 说明 |
|---|---|
| 配置 | `DEMO_ROOT` / `DEMO_ASTRBOT_BASE` / `DEMO_UMO` / `DEMO_DB` / `DEMO_MINIMAX_KEY_FILE` |
| 主动消息 | 角度池、间隔、配额、`last_sent` 落盘、禁用开关 |
| B 站 | 搜索/热门 API、截图、失败回落 |
| 语音 | 文本→TTS→音频，比例控制，TTS 失败回退文字 |
| 发送 | 调 AstrBot IM API，多段发送，返回码判定 |
| 可靠性 | PID 单实例锁、启动等 AstrBot 就绪、atexit 清理 |

### 5.2 patches/files/astrbot（30 个文件）

| 文件 | 作用 |
|---|---|
| `core/utils/send_ledger.py` | 发送台账状态机 |
| `core/utils/outbound_gate.py` | 出站门禁 |
| `core/utils/life_state.py` | 生活层状态机 |
| `core/utils/memory_facts.py` | 结构化记忆 |
| `core/utils/mood_state.py` | 情绪状态 |
| `core/utils/style_profile.py` / `style_rag.py` | 风格目标分布与检索 |
| `core/utils/serious_probe.py` | 认真话题探针 |
| `core/utils/history_sanitize.py` / `history_exclude.py` | 历史清洗/排除 |
| `core/utils/event_memory.py` | 事件记忆 |
| `core/utils/tic_budget.py` / `length_nudge.py` | 口癖预算 / 长度提示 |
| `core/utils/tts_colloquial.py` | TTS 口语化预处理 |
| `core/platform/sources/aiocqhttp/*` | 入站事件、发送路径 |
| `core/pipeline/message_merge.py` | 多段消息合并 |
| `core/pipeline/.../internal.py` | Agent 内部阶段 |
| `core/pipeline/result_decorate/stage.py` | 结果装饰 |
| `core/astr_main_agent.py` | 主 Agent |
| `core/persona_mgr.py` | 配置管理 |
| `core/provider/manager.py` | Provider 管理 |
| `core/provider/sources/minimax_*` | MiniMax TTS / Embedding |
| `dashboard/services/proactive_service.py` | 主动消息 API |
| `dashboard/services/open_api_service.py` | 内部 OpenAPI |
| `dashboard/api/open_api.py` | OpenAPI 路由 |
| `core/event_bus.py` | 事件总线 |
| `core/agent/message.py` | 消息模型 |

> 这些是「改过的文件副本」，不是 AstrBot 全量源码。应用方式见 `patches/README.md`。

### 5.3 ops/

| 目录 | 作用 |
|---|---|
| `life/` | 事件模型、状态、影子、审计、SPEC |
| `memory/` | 候选事实、结构化记忆演示 |
| `mood/` | 情绪状态演示 |
| `eval/` | 基准、judge、校准、相似度、冻结集、盲测 |
| `verify/` | 发布门禁、每日体检、观察、清理、健康检查 |
| `backup/` | 备份、校验、恢复演练、加密、轮换 |
| `deploy/` | 安装、watchdog、构建上下文、依赖锁 |
| `docker/` | 容器内自检 |
| `security/` | 密钥轮换、日志扫描、备份脱敏 |
| `patches/` | 补丁导出/应用/校验 |
| `protect/` | 快照与清单 |
| `isolate/` | FakeBot、生产库隔离 |
| `kb/` | 知识库索引/计数/清理 |
| `tests/` | 97 个测试文件（P0/P1/Q* 系列） |

---

## 6. 关键设计决策

### 6.1 为什么用 SQLite 而不是 Redis/Postgres

- 单机、单用户、低并发；
- 需要「能打包、能校验、能恢复」；
- 备份就是一个文件 + 一份 manifest；
- 事务足够表达发送台账的并发认领。

代价：跨进程写需要 WAL + busy_timeout；测试里复制库必须用 `_dbcopy.snapshot()`，不能直接 `copy2`。

### 6.2 为什么把风格规则做成确定性门禁

prompt 里写「回复要短」模型不一定听；写成「超过 N 字就截断/重写」又很粗暴。折中：

- 规则只做**否决/降权**，不负责生成；
- 规则有单测，改规则必须跑门禁；
- 规则阈值来自语料统计，但不是每条都硬卡。

### 6.3 为什么有「单改权」锁

两个人（或两个 agent）同时改配置，A/B 结果不可信。`persona_lock.py` 用文件锁 + sha256 留痕，锁 4 小时过期，`status` 能看出「配置改了但没跑 bench」。

### 6.4 为什么先影子模式

生活层、情绪层、记忆注入都先 `*_SHADOW=1`：只记录、不注入。跑够 24h 看日志，再逐项打开。避免「一上线就把配置改坏，还找不到是哪一层」。

---

## 7. 已知边界

- 依赖 AstrBot 4.29.1 的内部结构，升级需要重新导出补丁；
- Windows 路径和 PowerShell 计划任务占了很大比重，Linux 部署需要改路径/服务；
- 生活层默认关闭，24h 模板是测试生成的四天，不是长期日历；
- 没有真实定位源，地点只作为上下文；
- 评测的私有 holdout / 真实语料不随仓库发布，分数不可直接复现；
- 示例配置是虚构的，不代表原始配置。

---

## 8. 延伸阅读

- [EVAL.md](EVAL.md)：评测方法论与噪声下限
- [OPERATIONS.md](OPERATIONS.md)：启动、watchdog、备份、恢复
- [SECURITY.md](SECURITY.md)：密钥、日志、备份脱敏
- [SANITIZATION.md](SANITIZATION.md)：公开版删了什么、替换了什么
- [DEPLOY.md](DEPLOY.md)：从零部署
