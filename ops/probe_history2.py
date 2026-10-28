# -*- coding: utf-8 -*-
"""只读：找出历史里最大的几条消息。"""
import json
import sqlite3

DB = r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
row = db.execute(
    "select content from conversations where user_id='aiocqhttp:FriendMessage:100000000'"
).fetchone()
hist = json.loads(row[0])
sized = []
for i, m in enumerate(hist):
    c = m.get("content")
    s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    sized.append((len(s), i, m.get("role"), s))
sized.sort(reverse=True)
total = sum(x[0] for x in sized)
print("total chars:", total)
for ln, i, role, s in sized[:5]:
    print(f"\n#{i} role={role} chars={ln} ({ln*100//total}%)")
    print(repr(s[:300]))
    print("  ...", repr(s[-200:]))
print("\nsmallest 3:", [(x[0], x[2]) for x in sized[-3:]])
print("median:", sized[len(sized)//2][0])

