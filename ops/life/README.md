# 生活层 · 旧影子实现（ops/life）

依据：`生活层_设计与接入契约_20261025.md`

> **权威实现已迁到** `instances/11111111-1111-4111-8111-111111111111/core/astrbot/core/utils/life_state.py`。
> 本目录的 `life.py` 只作测试夹具/历史参考；**不要用它的通过证明生产可用**，
> 也不要在两套实现上同时加功能。当前规格见 `SPEC.md`。

状态：**旧影子模式**——只建状态、只写日志，不发消息、不改线上人格、不调模型。

## 文件

| 文件 | 作用 |
|---|---|
| `schema.sql` | 7 张表 + 承诺激活约束（§9 / §7） |
| `life.py` | 存储、时钟、推进、上下文、正典、影子 |
| `canon.jsonl` | 7 条待审计正典（全部 candidate，value 不填） |
| `tests/test_life.py` | 18 个离线用例 |
| `life_shadow.db` | 影子库（可删，重建即可） |
| `shadow.jsonl` | 每次上下文快照（追加写） |

## 跑

```powershell
$py = 'D:/demo\instances\<id>\venv\Scripts\python.exe'
cd 'D:/demo\ops\life'
& $py life.py init        # 建库
& $py life.py advance     # 推进一次（确定性，无模型）
& $py life.py shadow      # 生成一次上下文快照
& $py life.py status      # 看世界状态
& $py tests\test_life.py  # 18 个用例
```

## 契约落点

| 契约 | 实现 |
|---|---|
| §3 UTC 存储、角色时区显示 | `iso()` / `local_date()`；库里全是 UTC |
| §3 夏令时不存在的时刻 | `resolve_local()` 返回 `nonexistent`，调用方必须跳过并记理由 |
| §3 重复时刻 | 返回 `ambiguous`（fold=0），偏移随 UTC 保存 |
| §7 draft 不能激活 | 两个 SQLite 触发器：`active` 必须有 `activation_task_id` |
| §9 幂等 | `UNIQUE(world_id, idempotency_key)` + `INSERT OR IGNORE` |
| §9 版本比较 | `UPDATE ... WHERE version=?`，rowcount≠1 抛 `VersionConflict` |
| §10 推进不调模型 | `advance()` 里没有任何 LLM 调用 |
| §10 离线聚合 | ≥30 小时只产生 1 条 `offline_gap` + 最多 1 条 `day` tick |
| §10 上下文预算 | 默认 1000 字，**按整行裁剪**，不切断半句 |
| §10 发送前校验 | `validate_context()` 查版本、有效期、降级 |
| §11 三档开关 | `SWITCHES` 默认全 False；影子层无网络/发送 import（有测试断言） |

## 明确没做

- 七个既有素材入口（`DAILY_TEMPLATES` / `gen_topic_fallback` / `_kb_hint` …）**未改**
- 未接 AstrBot 请求管线
- 未启用主动与延迟
- 正典 7 条**全部未审计**，因此上下文里不会出现任何具体地点/作息/关系断言
- 时区固定用 `Asia/Shanghai` 仅作夹具；契约要求生产前确认角色时区
