-- 生活层 schema v1
-- 依据：《生活层_设计与接入契约_20261025》§9
-- 契约不是已执行 SQL；本文件由 ops/life/life.py 在影子库上执行。
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- §9 source：五类来源 + 验证状态（两件事，不合并）
CREATE TABLE IF NOT EXISTS source (
  source_id           TEXT PRIMARY KEY,
  source_type         TEXT NOT NULL CHECK (source_type IN
                        ('real_archive','user_statement','virtual','external','model_inference')),
  ref                 TEXT,            -- 原消息 ID / bvid / 文件哈希#行号
  speaker             TEXT,
  occurred_at         TEXT,            -- UTC ISO8601
  fetched_at          TEXT,
  read_scope          TEXT,
  raw_sha             TEXT,
  verification_status TEXT NOT NULL CHECK (verification_status IN
                        ('candidate','confirmed','retracted','superseded')),
  visibility_scope    TEXT NOT NULL DEFAULT 'private'
);

-- §9 world_state：一个世界一份状态，不按 UMO 分裂
CREATE TABLE IF NOT EXISTS world_state (
  world_id         TEXT PRIMARY KEY,
  character_id     TEXT NOT NULL,
  version          INTEGER NOT NULL DEFAULT 1,
  tz               TEXT NOT NULL,          -- 角色 IANA 时区
  activity_event_id TEXT,
  place_id         TEXT,
  energy           REAL NOT NULL DEFAULT 0.6 CHECK (energy BETWEEN 0 AND 1),
  global_mood      REAL NOT NULL DEFAULT 0.0 CHECK (global_mood BETWEEN -1 AND 1),
  last_advance_utc TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

-- §9 life_event：幂等键唯一，跨午夜保留完整 UTC 区间
CREATE TABLE IF NOT EXISTS life_event (
  event_id         TEXT PRIMARY KEY,
  world_id         TEXT NOT NULL REFERENCES world_state(world_id),
  idempotency_key  TEXT NOT NULL,
  source_id        TEXT REFERENCES source(source_id),
  kind             TEXT NOT NULL,          -- activity|transit|offline_gap|outcome|tick
  start_utc        TEXT NOT NULL,
  end_utc          TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('planned','active','paused','done','cancelled','missed','unknown')),
  visibility_scope TEXT NOT NULL DEFAULT 'private',
  payload          TEXT NOT NULL DEFAULT '{}',
  revision         INTEGER NOT NULL DEFAULT 0,
  UNIQUE (world_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_life_event_window ON life_event(world_id, start_utc, end_utc);

-- §9 commitment：draft 不能激活；activation_task_id + 序号唯一
CREATE TABLE IF NOT EXISTS commitment (
  commitment_id      TEXT PRIMARY KEY,
  world_id           TEXT NOT NULL REFERENCES world_state(world_id),
  seq                INTEGER NOT NULL,
  audience_scope     TEXT NOT NULL DEFAULT 'private',
  text               TEXT NOT NULL,
  due_at             TEXT,
  status             TEXT NOT NULL CHECK (status IN
                       ('draft','active','blocked','fulfilled','cancelled','expired')),
  capability         TEXT,
  activation_task_id TEXT,
  completion_evidence TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (activation_task_id, seq)
);

-- §9 relation_state：按受众分，不共享私聊情绪
CREATE TABLE IF NOT EXISTS relation_state (
  world_id         TEXT NOT NULL REFERENCES world_state(world_id),
  subject_id       TEXT NOT NULL,
  scope            TEXT NOT NULL,
  relation_version INTEGER NOT NULL DEFAULT 1,
  mood             REAL NOT NULL DEFAULT 0.0 CHECK (mood BETWEEN -1 AND 1),
  reason_event_id  TEXT REFERENCES life_event(event_id),
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (world_id, subject_id, scope)
);

-- §9 processed_event：防消息重放反复改状态
CREATE TABLE IF NOT EXISTS processed_event (
  processor      TEXT NOT NULL,
  event_id       TEXT NOT NULL,
  applied_at     TEXT NOT NULL,
  result_version INTEGER,
  PRIMARY KEY (processor, event_id)
);

-- §9 fact_version：更正新增版本，不覆盖原始证据
CREATE TABLE IF NOT EXISTS fact_version (
  fact_id     TEXT NOT NULL,
  revision    INTEGER NOT NULL,
  subject     TEXT NOT NULL,
  predicate   TEXT NOT NULL,
  value       TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('single','multi')),
  source_id   TEXT REFERENCES source(source_id),
  valid_from  TEXT,
  valid_to    TEXT,
  supersedes  TEXT,
  status      TEXT NOT NULL CHECK (status IN
                ('candidate','confirmed','retracted','superseded')),
  scope       TEXT NOT NULL DEFAULT 'private',
  PRIMARY KEY (fact_id, revision)
);

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');

-- §7：draft 不能直接变成 active；必须由平台确认的 task 激活
CREATE TRIGGER IF NOT EXISTS trg_commitment_insert_active
BEFORE INSERT ON commitment
WHEN NEW.status = 'active' AND NEW.activation_task_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'commitment: active requires activation_task_id');
END;

CREATE TRIGGER IF NOT EXISTS trg_commitment_update_active
BEFORE UPDATE OF status ON commitment
WHEN NEW.status = 'active' AND NEW.activation_task_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'commitment: active requires activation_task_id');
END;