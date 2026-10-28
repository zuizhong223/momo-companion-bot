#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生活层影子实现的自检（离线，不碰网络、不碰线上库）。

覆盖契约里的硬约束：
  §3  时区 / 夏令时不存在与重复时刻
  §9  表结构、唯一键、版本比较
  §10 推进幂等、离线 30 小时聚合、build_context / validate_context
  §11 影子模式不发送
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import life  # noqa: E402

UTC = timezone.utc


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="life_test_")
        self.db = Path(self.tmp) / "t.db"
        self.conn = life.connect(self.db)
        life.migrate(self.conn)
        life.ensure_world(self.conn, "w1", "demo", "Asia/Shanghai",
                          at=utc("2026-10-25T00:00:00"))

    def tearDown(self):
        self.conn.close()

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class T01_Schema(Base):
    def test_tables_and_version(self):
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("world_state", "life_event", "source", "commitment",
                  "relation_state", "processed_event", "fact_version", "meta"):
            self.assertIn(t, names)
        self.assertEqual(
            self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "1")
        self.assertEqual(self.count("world_state"), 1)
        for t in ("life_event", "source", "commitment",
                  "relation_state", "processed_event", "fact_version"):
            self.assertEqual(self.count(t), 0)


class T02_Idempotency(Base):
    def test_event_key_unique(self):
        a = life.emit_event(self.conn, "w1", "k1", "tick", "2026-10-25T00:00:00Z",
                            "2026-10-25T01:00:00Z", "done")
        b = life.emit_event(self.conn, "w1", "k1", "tick", "2026-10-25T00:00:00Z",
                            "2026-10-25T01:00:00Z", "done")
        self.assertIsNotNone(a)
        self.assertIsNone(b, "同一幂等键必须只写一次")
        self.assertEqual(self.count("life_event"), 1)


class T03_Advance(Base):
    def test_forward_bumps_version_and_decays_energy(self):
        r = life.advance(self.conn, "w1", utc("2026-10-25T10:00:00"), expected_version=1)
        self.assertTrue(r["advanced"])
        self.assertEqual(r["version"], 2)
        w = life.get_world(self.conn, "w1")
        self.assertLess(w["energy"], 0.6)
        self.assertEqual(w["last_advance_utc"], "2026-10-25T10:00:00Z")

    def test_backward_is_noop(self):
        r = life.advance(self.conn, "w1", utc("2026-10-24T10:00:00"))
        self.assertFalse(r["advanced"])
        self.assertEqual(life.get_world(self.conn, "w1")["version"], 1)

    def test_version_conflict(self):
        with self.assertRaises(life.VersionConflict):
            life.advance(self.conn, "w1", utc("2026-10-25T10:00:00"), expected_version=99)
        self.assertEqual(life.get_world(self.conn, "w1")["version"], 1, "冲突不得改状态")


class T04_OfflineGap(Base):
    def test_30h_gap_aggregates(self):
        start = utc("2026-10-25T00:00:00")
        r = life.advance(self.conn, "w1", start + timedelta(hours=30))
        self.assertTrue(r["advanced"])
        self.assertLessEqual(len(r["events"]), 2, "离线只能产生少量聚合事件")
        kinds = [row[0] for row in self.conn.execute("SELECT kind FROM life_event")]
        self.assertIn("offline_gap", kinds)
        self.assertEqual(self.count("life_event"), len(r["events"]))

    def test_two_day_tick_not_flood(self):
        start = utc("2026-10-25T00:00:00")
        life.advance(self.conn, "w1", start + timedelta(days=2))
        # 只允许：1 条 offline_gap + 1 条 day tick
        self.assertLessEqual(self.count("life_event"), 2)


class T05_Timezone(Base):
    def test_nonexistent_local_time(self):
        utc_dt, note = life.resolve_local("Europe/Berlin",
                                          datetime(2026, 3, 29, 2, 30))
        self.assertIsNone(utc_dt)
        self.assertEqual(note, "nonexistent")

    def test_ambiguous_local_time(self):
        utc_dt, note = life.resolve_local("Europe/Berlin",
                                          datetime(2026, 10, 25, 2, 30))
        self.assertIsNotNone(utc_dt)
        self.assertEqual(note, "ambiguous")

    def test_normal_local_time(self):
        utc_dt, note = life.resolve_local("Asia/Shanghai",
                                          datetime(2026, 10, 25, 12, 0))
        self.assertEqual(note, "ok")
        self.assertEqual(utc_dt.hour, 4)

    def test_local_date_across_midnight(self):
        # ExampleCity 10-26 00:30 = UTC 10-25 16:30
        self.assertEqual(life.local_date(utc("2026-10-25T16:30:00"), "Asia/Shanghai"),
                         "2026-10-26")


class T06_Context(Base):
    def test_degraded_when_no_world(self):
        snap = life.build_context(self.conn, "missing", "umo", utc("2026-10-25T10:00:00"))
        self.assertTrue(snap["degraded"])
        self.assertEqual(snap["reason"], "no_world_state")

    def test_normal_snapshot_fields(self):
        life.advance(self.conn, "w1", utc("2026-10-25T10:00:00"))
        snap = life.build_context(self.conn, "w1", "umo", utc("2026-10-25T10:05:00"))
        self.assertFalse(snap["degraded"])
        self.assertEqual(snap["state_version"], 2)
        self.assertTrue(snap["valid_until_utc"])
        self.assertIn("allowed_claims", snap)
        for claim in snap["allowed_claims"]:
            self.assertIn("event_id", claim, "每条可陈述断言都要能追到事件")

    def test_valid_until_and_version_check(self):
        life.advance(self.conn, "w1", utc("2026-10-25T10:00:00"))
        snap = life.build_context(self.conn, "w1", "umo", utc("2026-10-25T10:05:00"))
        ok, why = life.validate_context(snap, utc("2026-10-25T10:05:00"),
                                        life.get_world(self.conn, "w1"))
        self.assertTrue(ok, why)

        # 过期
        ok, why = life.validate_context(snap, utc("2026-10-25T23:00:00"),
                                        life.get_world(self.conn, "w1"))
        self.assertFalse(ok)

        # 版本已变
        life.advance(self.conn, "w1", utc("2026-10-25T11:00:00"))
        ok, why = life.validate_context(snap, utc("2026-10-25T11:00:01"),
                                        life.get_world(self.conn, "w1"))
        self.assertFalse(ok)
        self.assertEqual(why, "state_version_changed")


class T07_Shadow(Base):
    def test_shadow_writes_and_does_not_send(self):
        log = Path(self.tmp) / "shadow.jsonl"
        snap = life.shadow_once(self.conn, "w1", "umo", utc("2026-10-25T10:00:00"), log)
        self.assertFalse(snap["degraded"])
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertIn("snapshot", rec)
        self.assertFalse(any(rec["switches"].values()), "三档开关默认必须全关")

    def test_no_network_imports(self):
        src = (HERE.parent / "life.py").read_text(encoding="utf-8")
        for bad in ("import requests", "import aiohttp", "from aiocqhttp",
                    "send_by_session", "urllib.request"):
            self.assertNotIn(bad, src, f"影子层不得引入发送/网络能力：{bad}")


class T08_Canon(Base):
    def test_seven_items_all_pending(self):
        items = life.load_canon()
        self.assertEqual(len(items), 7)
        self.assertTrue(all(i["status"] != "confirmed" for i in items))
        self.assertTrue(all(i["value"] is None for i in items),
                        "未审计前不得填 value")
        self.assertTrue(all(i.get("audit_note") for i in items))

    def test_canon_ids_unique(self):
        ids = [i["fact_id"] for i in life.load_canon()]
        self.assertEqual(len(ids), len(set(ids)))


def run_all() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
