# 安全说明

> 这个仓库是脱敏公开版，不包含任何可用的密钥、聊天记录或个人信息。
> 但原始运行环境里的密钥曾经以明文形式存在过，**公开前/后都应该轮换**。

---

## 1. 威胁模型

这是一个单机、单用户的 QQ 对话机器人，主要风险不是「被 APT 打」，而是：

| 风险 | 场景 | 后果 |
|---|---|---|
| 密钥泄露 | `.env` / `cmd_config.json` / 日志 / 截图 / git 历史 | 别人可以刷你的 API 额度 |
| 聊天记录泄露 | 数据库、导出文本、备份、训练数据 | 第三方隐私泄露 |
| 管理接口暴露 | Dashboard / OpenAPI 绑定到 `0.0.0.0` | 别人可以改配置、发消息 |
| 账号风险 | QQ 小号被风控/封禁 | 机器人掉线 |
| 错误重试 | `unknown` 状态自动重试 | 重复消息轰炸 |
| 备份未加密 | 整包 zip 放到网盘 | 数据库/配置泄露 |

---

## 2. 密钥放在哪

| 位置 | 内容 | 是否进 git | 处理 |
|---|---|---|---|
| `.env` | provider key、Dashboard 密码、内部 OpenAPI key | 否 | `.gitignore` 排除，只提交 `.env.example` |
| `local_keys.py` | 从 `.env` 读取，不硬编码 | 是（安全） | 只读环境变量 |
| `deepseek_key.txt` / `minimax_key.txt` | 历史遗留明文 | 否 | 迁移后删除，改用 `.env` |
| `cmd_config.json` | Dashboard JWT、密码哈希、ws token、provider key | 否 | 备份时写成 `REDACTED` |
| `backups/secrets/` | 历史配置备份 | 否 | 加密归档或销毁 |
| 日志 | 可能带 key/手机号 | 否 | `scan_logs.py` 扫描 + `_redact()` |

加载方式：

```python
# local_keys.py
ENV = _load_env(Path(__file__).with_name(".env"))
OPENAPI_KEY = ENV.get("OPENAPI_KEY", "")
```

代码里不出现 key 字面量；`.env` 不进 git；`.env.example` 只给空值。

---

## 3. 轮换顺序

```powershell
# 1) 看当前 key 存在哪几处
python ops\dev\rotate_key.py --check

# 2) 生成新 key，先换上
python ops\dev\rotate_key.py --name deepseek

# 3) 验证新 key 真能调通
python ops\dev\rotate_key.py --verify

# 4) 确认新 key 工作后，再撤销旧 key
```

顺序不能反：先删旧的会导致服务中断，且无法回滚。

要轮换的至少包括：

- DeepSeek / OpenAI 兼容 provider key；
- Dashboard 密码 / JWT secret；
- 内部 OpenAPI key；
- NapCat WebUI token / QQ 登录凭据；
- 任何曾经出现在聊天、截图、日志里的 key。

---

## 4. 日志脱敏

`ops/security/scan_logs.py` 扫描：

- `sk-...` / `Bearer ...`；
- 手机号；
- 邮箱；
- 长 token。

发送路径里也有 `_redact()`（`butler.py`）在写日志前替换 `sk-***`。

注意：日志脱敏是**兜底**，不是保险。真正的原则是「不要 log 请求体/密钥」。

---

## 5. 备份脱敏

`ops/backup/backup.py` 在打包时：

- 把 `cmd_config.json` 里的 provider key、TTS key、embedding key 写成 `REDACTED`；
- 不打包 `.env`、`*_key.txt`、`backups/secrets/`；
- 生成 `manifest.json` 记录文件 sha256。

恢复时必须补钥匙：

```powershell
python ops\dev\rotate_key.py --check
```

> 备份里的 `REDACTED` 是设计，不是 bug。看到它不要以为备份坏了。

---

## 6. Git 历史

原始私有仓库曾经把 `local_keys.py`、`cmd_config.json` 提交进历史。发现后做了两件事：

1. 从 index 移除，加 `.gitignore`；
2. 轮换所有曾进入历史的 key。

`ops/security/GIT_SECRET_HISTORY.md`（私有版）记录了哪些 commit 有残留。**公开版没有推送原始 git 历史**，是从空历史重新初始化的，所以旧 key 不会通过 commit 泄露。但旧 key 本身仍然应该视为已泄露并轮换。

检查工具：

```powershell
python ops\verify\check_git_secrets.py
```

它扫 `git ls-files` + 常见密钥正则；命中退出码非 0。

---

## 7. 网络暴露面

默认只监听本机：

- AstrBot Dashboard：`127.0.0.1:22877`；
- NapCat OneBot WS：`127.0.0.1:6199`；
- 内部 OpenAPI：`127.0.0.1:22877/api/v1/...`。

如果一定要公网访问：

- 不要直接把 22877/6199 暴露到公网；
- 用反向代理 + TLS + 强密码 + IP 白名单；
- 只暴露必要的 API，Dashboard 管理面单独限制；
- 定期看 `ops/security/` 的轮换记录。

---

## 8. 权限边界

- QQ 小号只用于机器人，不用主号；
- 机器人不执行任意系统命令；
- 截图只存本机 `screens/`，不自动外发；
- 群聊只在自己有权限的群使用；
- 不主动私聊陌生人，不做大规模自动化。

---

## 9. 如果发现泄露

1. 立即轮换对应 key；
2. 检查 `git log -p` / 备份 / 日志 / 截图；
3. 用 `git filter-repo` 清历史（需要明确授权和维护窗口）；
4. 通知受影响的人；
5. 补一条 `ops/security/` 记录，写清楚「怎么泄露的、怎么修的」。

---

## 10. 公开版声明

- 仓库内**没有**有效密钥；
- 仓库内**没有**示例对话记录、配置原文、音色、模型、截图；
- 仓库内的人名/地名/QQ/路径均为占位符；
- 示例配置与示例对话完全虚构；
- 如果你发现任何疑似真实信息，请开 issue，我会立刻处理。
