# judge 人工校准（B04）

> 状态：**模板已生成，人工标签未填**。本流程不需要调用任何模型。

## 文件

- `ops/eval/judge_calibrate.py`：生成/比较脚本
- `ops/eval/reports/judge_calibration_20.csv`：20 条样本，模型回复已填，人工三列空白
- `ops/eval/reports/judge_calibration_20.md`：同一批样本的可读版，方便直接看/填

## 步骤

1. 生成（可重跑，固定 seed）：
   ```powershell
   $py = "D:/demo\instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe"
   & $py ops\eval\judge_calibrate.py --build
   ```
2. 人工给每条打 `human_relevance / human_in_character / human_naturalness`（1–5），可写 `human_note`；可先看 `judge_calibration_20.md`。
3. 比较：
   ```powershell
   & $py ops\eval\judge_calibrate.py --compare
   ```
   输出精确一致率与 ±1 一致率。

## 判定

- 如果 `in_character` 的 ±1 一致率 < 70%，judge 不能作为“像机器人”的主要验收；
- 如果 `relevance` 在事实题上把“编造具体时间”打高分，先修 rubric/给 reference，再谈分数；
- 校准集要保留，不要每次调参都换新样本；调参用训练集，验收用这份 + `11_heldout`。

## 备注

- 样本来自 `reports/09_c.json`（45 条），按类别分层抽 20 条；
- 这些是**旧版本输出**，用于校准 judge，不代表当前版本；
- 填完人工标签后，再决定是否调用付费 judge 对齐。
