# -*- coding: utf-8 -*-
"""只读：分析会话历史体积（估算 token / 轮次）。"""
import json
import sqlite3

DB = r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
row = db.execute(
    "select conversation_id, content, updated_at from conversations "
    "where user_id='aiocqhttp:FriendMessage:100000000'"
).fetchone()
print("conversation_id:", row[0])
print("updated_at:", row[2])
hist = json.loads(row[1])
print("messages:", len(hist))
kinds = {}
for m in hist:
    kinds[m.get("role")] = kinds.get(m.get("role"), 0) + 1
print("roles:", kinds)
chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in hist)
print("raw json chars:", chars, "approx tokens:", chars // 2)
print("\n--- last 6 messages ---")
for m in hist[-6:]:
    c = m.get("content")
    s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    print(f"[{m.get('role')}] {s[:200]!r}")
print("\n--- first 3 messages ---")
for m in hist[:3]:
    c = m.get("content")
    s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    print(f"[{m.get('role')}] {s[:200]!r}")

