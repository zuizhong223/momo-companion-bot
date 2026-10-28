# AstrBot 补丁

这里放的是**改过的 AstrBot 4.29.1 核心文件副本**，不是完整框架，也不是 `git diff`。

## 为什么是整文件

- AstrBot 升级后，按文件覆盖比按 diff 打补丁更稳；
- 每个文件都能单独 review；
- 出问题可以整文件回滚。

代价是：上游一改，补丁就可能冲突，需要手动合并。

## 目录

```
patches/
└── files/
    └── astrbot/
        ├── astr_main_agent.py
        ├── core/
        │   ├── agent/
        │   ├── pipeline/
        │   ├── platform/sources/aiocqhttp/
        │   ├── provider/
        │   └── utils/
        └── dashboard/
            ├── api/
            └── services/
```

## 应用

```powershell
# 先备份
Copy-Item <ASTRBOT_INSTANCE>\core\astrbot `
          <ASTRBOT_INSTANCE>\core\astrbot.bak -Recurse

# 覆盖
Copy-Item patches\files\astrbot\* `
          <ASTRBOT_INSTANCE>\core\astrbot\ -Recurse -Force
```

## 校验

原始私有版有 `MANIFEST.sha256`，记录每个文件的 sha256；公开版没有放 manifest（因为文件已经脱敏，哈希与原文件不一致）。

如果要自己维护：

```powershell
python ops\protect\snapshot.py --manifest-only
python ops\patches\patches.py verify
```

## 文件分组

| 组 | 文件 | 作用 |
|---|---|---|
| 发送 | `core/utils/send_ledger.py`、`core/platform/sources/aiocqhttp/aiocqhttp_message_event.py` | 台账状态机 / 发送路径 |
| 门禁 | `core/utils/outbound_gate.py`、`length_nudge.py`、`tic_budget.py`、`tts_colloquial.py` | 发送前规则 |
| 生活 | `core/utils/life_state.py` | 事件、状态、延迟、地点 |
| 记忆 | `core/utils/memory_facts.py`、`event_memory.py`、`history_sanitize.py`、`history_exclude.py` | 事实/事件/历史 |
| 情绪 | `core/utils/mood_state.py` | 情绪衰减与注入 |
| 风格 | `core/utils/style_profile.py`、`style_rag.py`、`serious_probe.py` | 风格目标 / 检索 / 认真话题 |
| 管线 | `core/pipeline/message_merge.py`、`.../internal.py`、`result_decorate/stage.py`、`core/agent/message.py` | 多段合并 / Agent 阶段 |
| Provider | `core/provider/manager.py`、`core/provider/sources/minimax_*` | Provider / TTS / Embedding |
| 管理 | `dashboard/services/proactive_service.py`、`open_api_service.py`、`dashboard/api/open_api.py` | 主动消息 / 内部 API |
| 其他 | `core/event_bus.py`、`core/persona_mgr.py`、`astr_main_agent.py`、`dashboard/schemas.py` | 事件 / 配置 / 主 Agent |

## 许可

这些文件是 AstrBot 的修改副本，遵循 AstrBot 的 **AGPL-3.0**。见仓库根目录 `LICENSE` 与 `NOTICE`。
