# 生活层规格 v1 · 2026-10-27（23:00 校正）

> 对象：示例角色（合成）
> 口径：**生产权威实现是 `instances/.../core/astrbot/core/utils/life_state.py`**；
> `ops/life/life.py` 是旧影子实现，只作测试/历史参考，不能拿它的通过证明生产可用。
> 当前生产：`DEMO_LIFE=1` + `DEMO_LIFE_SHADOW=1`，虚拟生活写 `data/life_shadow.db`，
> 关系记忆写正式库；影子不进 prompt。
> 数据：示例影子库（合成，不是真实记录）；`tz=Asia/Shanghai`。

---

## 1. 事件模型

表 `yy_life_event`：

| 字段 | 含义 |
|---|---|
| `event_id` | `<umo>:<date>:<kind>:<hhmm>`，可读、可重放 |
| `kind` | sleep / free / meal / class / practice / game / anime |
| `title` | 中文名（睡觉、上课、练习…） |
| `start_at` / `end_at` | UTC 时间 |
| `status` | planned → active → done / missed；`forgotten` 是软遗忘 |
| `told_at` | 已告诉用户的时间（只写一次） |
| `forgotten_at` | 软遗忘时间；可 `restore_event` 恢复 |
| `source_type` | virtual / user_statement / schedule |
| `place_id` | home / school / studio… |

生命周期：

```
planned --(到点)--> active --(结束)--> done
   \--(离线没赶上)--> missed
active/planned --(TTL 7 天)--> forgotten --(restore)--> planned
```

## 2. 24h 模板（当前，Asia/Shanghai）

```
00:30-07:00 sleep（以 life_state.SLEEP_START_H/SLEEP_END_H 为准）
07:00-12:00 free
12:00-12:40 meal
13:00-17:00 class（工作日）
18:00-18:40 meal
19:30-21:30 anime / game（按天轮换）
20:00-21:30 practice（示例）
21:30-23:00 free（示例）
free 填满其余空档
```

审计：`python ops\life\audit_events.py`，当前 **无 >30min 空档**，覆盖 4 天。
**时区决定（2026-10-27）：先用 `Asia/Shanghai`。** 理由：运营者在ExampleCity，主动时段 9:00-23:00 与聊天同步；代价是机器人的配置是德国，生活时间不是德国时间。若以后要 `Europe/Berlin`，必须同时平移主动时段和睡眠窗口，不能只改 tz。

## 3. 睡眠 / 延迟（必须回复）

- **不静默**：任何情况下都必须回（运营者明确要求）。
- 睡眠窗口（00:30-07:00）不禁止回复，只把延迟拉长：建议 2~10 分钟，并在开头说“刚醒/刚看到”。
- 主动消息只在 09:00-23:00；睡眠时段不发。
- 普通回复延迟 1~90 秒（已上线），睡眠窗口另加长延迟。
- 不做“已读不回”。

## 4. 遗忘

- **虚拟细节**（吃了什么、看了哪集、天气）：TTL 7 天 → `status=forgotten`，`forgotten_at` 记录；可 `restore_event`。
- **已经 told 的虚拟事件不遗忘**（说过的经历要能跨日回忆）；`told_at IS NOT NULL` 的行跳过 TTL。
- **事实**（生日、名字、承诺、关系）：不遗忘；`yy_fact` 只增不删，撤回用 `retracted`。
- **情节**：按天/周摘要，摘要保留；原始细节可遗忘。
- 目标：事实稳定、细节模糊、模糊本身一致（“记不太清”而不是编）。

测试：`ops\life\test_forget.py`（临时库，不动生产）。

## 5. 地点

- `set_place` 只接受 `user_statement` / `schedule` 来源，置信度 ≥0.7，TTL 8h。
- **没有真实定位源**：不做“你在哪/我来接你”这类主动消息。
- 地点只作为上下文提示，不作为事实断言。

## 6. 验收

| 项 | 判据 |
|---|---|
| 24h 覆盖 | `audit_events.py` 无 >30min 空档 |
| 影子 | `DEMO_LIFE=1` + `DEMO_LIFE_SHADOW=1` 跑 24h，日志可抽查 |
| told | 只有实际发送成功且文案提到事件，才写 `told_at + task_id + message_id`；失败/unknown/未提及不写 |
| forgotten | 7 天后虚拟事件 `status=forgotten`，可 restore |
| 地点 | 0 条无来源的地点断言 |
| 跨天 | 跨午夜不跳变；回拨不倒退 |

## 7. 已知缺口

- 生活层默认关；影子未正式跑 24h。
- `told_at=0`、`forgotten_at=0`（因为没启用，不是 bug）。
- 时区是ExampleCity，配置是德国；未决策。
- 位置只有 `virtual` 来源，没有真实定位。
- 24h 模板是测试生成的 4 天，不是长期日历（考试周/演出季未接）。
