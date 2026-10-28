# 改动纪律（防覆盖）

> 起因：2026-10-26 15:40，另一个 agent 覆盖了 `aiocqhttp_message_event.py`，
> R02 的多段接线静默消失，而 43 个测试全部通过。

## 每次改核心文件，按这个顺序

```powershell
# 1) 动手前先看有没有别人的未提交改动
git status --porcelain

# 2) 改完立刻跑一条命令（刷新补丁清单 + 门禁 + 提交）
powershell -NoProfile -File ops\dev\after_edit.ps1 -Message "fix(xxx): 改了什么"
```

`after_edit.ps1` 会做三件事：

1. 重新计算 `patches/MANIFEST.txt` 里所有文件的 sha256，并把变动同步到 `patches/files/`
2. 跑 `ops/verify/release_gate.py`（失败就**不提交**）
3. 提交 `patches/` 与 `ops/tests`、`ops/verify`

## 提交信息要写"改了什么"

```
fix(R02): 多段整轮清单 start_turn/prepare_turn_commit
```

括号里带编号，方便 `git log --grep=R02` 找回。

## 覆盖的典型征兆

| 征兆 | 含义 |
|---|---|
| 某个功能"没生效"但测试全绿 | 接线被覆盖（N-5 会抓） |
| `patches verify` 通过但行为变了 | 对方改完重新 export 了，清单被"洗白" |
| `yy_turn` / 某张表长期 0 行 | 要么路径窄，要么没接上**（先用 N-5 判断）** |

## 两个 agent 同时改时

- 谁改 `send_ledger.py`，谁就不改 `aiocqhttp_message_event.py`；
- 同一时间只让一个人动 `internal.py`；
- 冲突时**以 N-5 的检查结果为准**，不以"我记得改过"为准。

## 配置：单改权 + 必须带指纹

**配置同一时间只能有一个人在改。** 2026-10-27 两个人同时改，导致任何 A/B 都不可信
（一次"掉 0.61"最后证明是采样噪声）。

改完配置，必须用 `bench.py` 留指纹：

```powershell
python ops\eval\bench.py --cases holdout_v2_20261026.jsonl --runs 3
```

输出会记录 `file_sha` / `db_sha` / `chars`，以及每个指标的可分辨下限（mdd ≈ 2σ）。

### 当前锚点（2026-10-27）

| 指标 | 均值 ± σ | 可分辨下限 |
|---|---|---|
| 综合 | 4.25 ± 0.16 | ≥ 0.32 |
| 像机器人 | 4.23 ± 0.19 | ≥ 0.38 |
| 自然度 | 4.53 ± 0.19 | ≥ 0.38 |
| 接住 | 4.22 ± 0.13 | ≥ 0.26 |

**规则：差异小于可分辨下限的一律当噪声，不许写"变好了/变差了"。**

### 加锁用法（2026-10-27 起）

改配置前先加锁，改完跑 bench 再释放：

```powershell
python ops\dev\persona_lock.py acquire --who 你的名字 --why "改什么"
# …改 persona.txt…
python ops\eval\bench.py --cases holdout_v2_20261026.jsonl --runs 3
python ops\dev\persona_lock.py release
```

- 锁被占时 acquire 会拒绝；锁超过 4 小时算过期，可 `--force`
- `release` 会把 `sha_before / sha_after / 是否改动` 记进 `ops/persona_history.jsonl`
- `status` 会提示"配置和基准不一致 = 改过但没跑 bench"
- 每日体检里有"配置锁"一行；门禁有 `test_persona_lock.py`

**注意**：锁是约定，不是强制。两个 agent 都认它才有用。
