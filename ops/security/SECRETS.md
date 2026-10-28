# 凭据清单与轮换（迁移前置）

> 只列**位置与状态**，不写任何值。迁移/备份前必须逐项处理。

## 1. 现存位置

| 位置 | 内容 | 风险 | 状态 |
|---|---|---|---|
| `.env` | provider/API keys | 明文 | 未轮换 |
| `local_keys.py` | `OPENAPI_KEY` / `DEEPSEEK_KEY` 等 | 明文、被代码 import | 未轮换 |
| `deepseek_key.txt` | DeepSeek key | 明文 | 未轮换 |
| `minimax_key.txt` | MiniMax key | 明文 | 未轮换 |
| `napcat_cred.txt` | NapCat 凭据 | 明文 | 未轮换 |
| `instances/.../core/data/cmd_config.json` | dashboard `jwt_secret`、密码哈希、`ws_reverse_token`、provider key | 明文/哈希 | 未轮换 |
| `backups/secrets/` | 历史 `.env` / config 备份 | 明文 | 未加密 |
| `backups/*.zip` | 旧整包可能含 `.env`/内部 handoff | ZIP 未加密 | 未处理 |
| 内部 handoff / 文档 | 曾出现凭据 | 已进入不受控历史 | 需轮换 |

## 2. 轮换顺序

1. **先建新值**：在服务端/控制台生成新 key/token，不要先删旧值。
2. **改配置**：更新 `.env`、`local_keys.py`、`cmd_config.json`、`*_key.txt`；确认服务能用新值启动。
3. **撤销旧值**：确认新值工作后，撤销/删除旧 key、旧 token。
4. **清历史**：旧备份、旧 handoff、`backups/secrets/` 要么加密归档，要么销毁；不要只删 `.env` 就宣称脱敏。
5. **验证**：用旧 key 调用应失败；新 key 调用成功；dashboard 登录正常。

## 3. 备份加密

- 含数据库/配置的 zip 必须加密（至少 7z AES-256 或 age）；
- 密钥与备份分开存放；备份清单里不要写 key；
- 恢复演练时再用一次性临时目录解密，结束删除。

## 4. 验收

- 普通交接包不含有效凭据；
- 旧 key 失效、新 key 工作；
- 非预期来源无法访问管理接口；
- 备份加密可恢复；
- 文档/报告里不再出现 key/token 值。
