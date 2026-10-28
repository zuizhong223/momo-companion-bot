# -*- coding: utf-8 -*-
"""只读：导出第 88 条历史消息的结构（base64 折叠）。"""
import json
import re
import sqlite3

DB = r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
content = db.execute(
    "select content from conversations where user_id='aiocqhttp:FriendMessage:100000000'"
).fetchone()[0]
hist = json.loads(content)
msg = hist[88]
print("keys:", list(msg.keys()))
parts = json.loads(msg["content"])
print("parts:", len(parts))
for i, p in enumerate(parts):
    text = p.get("text", "")
    short = re.sub(r"[A-Za-z0-9+/=]{80,}", "«BASE64:%d»" % len(re.search(r"[A-Za-z0-9+/=]{80,}", text).group(0)) if re.search(r"[A-Za-z0-9+/=]{80,}", text) else "", text)
    print(f"--- part {i} type={p.get('type')} len={len(text)} id={p.get('id')}")
    print(short[:1200])

