"""SQLite fact store: one row per thing known about the user.

Not a chat log. Addressed by `(user_id, category, key)`, one record each, so
writing the same fact twice is an UPDATE by construction - "update and
invalidate, not just append" (ASSIGNMENT.md 4.2) is enforced by the primary
key, not by asking a model to be careful.

Two columns carry more weight than they look:

**version** - a monotonic counter per row, plus `store_version` across the
whole table. Writes are compare-and-set: a writer that read version 3 cannot
overwrite version 4. Today the agent is single-threaded and this never fires;
the day two turns run concurrently, or a session sweep races the per-turn
extractor, it is the difference between a lost update and a rejected one.

**deleted_at** - a tombstone. The row stays, the value goes. This is the
difference between "no employment information on record" and "I don't know",
which recall probe p04 asks for specifically: forgetting on request has to be
distinguishable from never having been told, or the user cannot tell whether
their deletion worked.

Every write records who wrote it, why, and how confident the proposer was, so
`session_diff()` can explain itself in one line per fact (ASSIGNMENT.md 4.4).
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import time

from Sora.Memory import schema

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "out" / "memory" / "sora_memory.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    user_id      TEXT NOT NULL,
    category     TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT,
    reason       TEXT,
    confidence   REAL,
    version      INTEGER NOT NULL DEFAULT 1,
    store_version INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    deleted_at   REAL,
    source       TEXT,
    session_id   TEXT,
    turn         INTEGER,
    PRIMARY KEY (user_id, category, key)
);
CREATE TABLE IF NOT EXISTS history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    category     TEXT NOT NULL,
    key          TEXT NOT NULL,
    op           TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT,
    confidence   REAL,
    decided_by   TEXT,
    session_id   TEXT,
    turn         INTEGER,
    at           REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_history_session ON history(session_id);
"""


def _merge(old_value, new_value) -> str:
    """Append semantics for list-like paths, deduplicated and capped."""
    parts = [p.strip() for p in str(old_value).split(";") if p.strip()]
    fresh = str(new_value).strip()
    lowered = [p.lower() for p in parts]
    if fresh.lower() in lowered or any(fresh.lower() in p for p in lowered):
        return str(old_value)
    parts.append(fresh)
    merged = "; ".join(parts)
    while len(merged) > schema.APPEND_MAX_CHARS and len(parts) > 1:
        parts.pop(0)          # oldest entry drops first
        merged = "; ".join(parts)
    return merged


def db_path(path=None) -> pathlib.Path:
    return pathlib.Path(path or os.environ.get("SORA_MEMORY_DB", str(DEFAULT_DB)))


class MemoryStore:
    def __init__(self, path=None, user_id="default"):
        self.path = db_path(path)
        self.user_id = user_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------- reading

    def get(self, category, key):
        row = self.conn.execute(
            "SELECT * FROM facts WHERE user_id=? AND category=? AND key=?",
            (self.user_id, category, key)).fetchone()
        return dict(row) if row else None

    def all(self, include_deleted=True) -> list:
        sql = "SELECT * FROM facts WHERE user_id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY category, key"
        return [dict(r) for r in self.conn.execute(sql, (self.user_id,))]

    def active(self) -> list:
        return self.all(include_deleted=False)

    def tombstones(self) -> list:
        return [f for f in self.all() if f["deleted_at"] is not None]

    def store_version(self) -> int:
        row = self.conn.execute("SELECT v FROM meta WHERE k='store_version'").fetchone()
        return int(row["v"]) if row else 0

    def _bump(self) -> int:
        version = self.store_version() + 1
        self.conn.execute("INSERT INTO meta(k,v) VALUES('store_version',?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(version),))
        return version

    # ------------------------------------------------------------- writing

    def apply(self, op, category, key, value=None, *, reason="", confidence=None,
              decided_by="", session_id="", turn=None, expected_version=None) -> dict:
        """Apply one accepted write. Returns the outcome record.

        `expected_version` makes it compare-and-set: pass the version you read
        and the write is rejected if somebody else moved first.
        """
        now = time.time()
        existing = self.get(category, key)
        if expected_version is not None and existing and existing["version"] != expected_version:
            return {"op": "conflict", "category": category, "key": key,
                    "reason": "expected version %s, found %s"
                              % (expected_version, existing["version"])}

        store_version = self._bump()
        old_value = existing["value"] if existing else None
        was_deleted = bool(existing and existing["deleted_at"])

        if op == "delete":
            if not existing:
                self.conn.commit()
                return {"op": "noop", "category": category, "key": key,
                        "reason": "nothing on record to delete"}
            if was_deleted:
                # Already tombstoned. Without this the per-turn pass and the
                # end-of-session sweep both "delete" it and the diff reports
                # the same forgetting twice, the second time with no old value.
                self.conn.commit()
                return {"op": "noop", "category": category, "key": key,
                        "reason": "already forgotten"}
            # Tombstone: keep the key, drop the value. p04 needs "no
            # information on record", which is not the same as "I don't know".
            self.conn.execute(
                "UPDATE facts SET value=NULL, deleted_at=?, updated_at=?, version=version+1,"
                " store_version=?, reason=?, confidence=?, source=?, session_id=?, turn=?"
                " WHERE user_id=? AND category=? AND key=?",
                (now, now, store_version, reason, confidence, decided_by, session_id, turn,
                 self.user_id, category, key))
            outcome = "deleted"
        elif existing:
            if schema.is_append(category, key) and not was_deleted and old_value and value:
                value = _merge(old_value, value)
            if not was_deleted and (old_value or "") == (value or ""):
                self.conn.commit()
                return {"op": "noop", "category": category, "key": key,
                        "reason": "value unchanged"}
            self.conn.execute(
                "UPDATE facts SET value=?, deleted_at=NULL, updated_at=?, version=version+1,"
                " store_version=?, reason=?, confidence=?, source=?, session_id=?, turn=?"
                " WHERE user_id=? AND category=? AND key=?",
                (value, now, store_version, reason, confidence, decided_by, session_id, turn,
                 self.user_id, category, key))
            outcome = "updated"
        else:
            self.conn.execute(
                "INSERT INTO facts(user_id,category,key,value,reason,confidence,version,"
                " store_version,created_at,updated_at,deleted_at,source,session_id,turn)"
                " VALUES(?,?,?,?,?,?,1,?,?,?,NULL,?,?,?)",
                (self.user_id, category, key, value, reason, confidence, store_version,
                 now, now, decided_by, session_id, turn))
            outcome = "added"

        self.conn.execute(
            "INSERT INTO history(user_id,category,key,op,old_value,new_value,reason,"
            " confidence,decided_by,session_id,turn,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.user_id, category, key, outcome, old_value, value, reason, confidence,
             decided_by, session_id, turn, now))
        self.conn.commit()
        return {"op": outcome, "category": category, "key": key, "value": value,
                "old_value": old_value, "reason": reason, "confidence": confidence}

    # -------------------------------------------------------------- diffs

    def session_diff(self, session_id) -> dict:
        """added / updated / deleted for one session, each with its reason.

        Read from `history`, not by diffing snapshots: the reason a fact
        changed is the interesting part of ASSIGNMENT.md 4.4, and it only
        exists at the moment of the write.
        """
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM history WHERE user_id=? AND session_id=? ORDER BY id",
            (self.user_id, session_id))]
        out = {"session_id": session_id, "added": [], "updated": [], "deleted": [],
               "noop": []}
        for row in rows:
            entry = {
                "path": "%s/%s" % (row["category"], row["key"]),
                "value": row["new_value"],
                "old_value": row["old_value"],
                "reason": row["reason"],
                "confidence": row["confidence"],
                "turn": row["turn"],
            }
            out.setdefault(row["op"], []).append(entry)
        return out

    def rejected_log(self, session_id, rejections) -> None:
        """Rejections are part of the write policy being inspectable: a fact
        that did not make it in is as interesting as one that did."""
        now = time.time()
        for item in rejections:
            self.conn.execute(
                "INSERT INTO history(user_id,category,key,op,old_value,new_value,reason,"
                " confidence,decided_by,session_id,turn,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.user_id, item.get("category", "?"), item.get("key", "?"), "rejected",
                 None, item.get("value"), item.get("rule_reason") or item.get("reason"),
                 item.get("confidence"), item.get("decided_by", "policy"), session_id,
                 item.get("turn"), now))
        self.conn.commit()

    def rejections(self, session_id) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM history WHERE user_id=? AND session_id=? AND op='rejected'"
            " ORDER BY id", (self.user_id, session_id))]

    def stats(self) -> dict:
        facts = self.all()
        return {"total": len(facts),
                "active": sum(1 for f in facts if f["deleted_at"] is None),
                "tombstones": sum(1 for f in facts if f["deleted_at"] is not None),
                "store_version": self.store_version(),
                "path": str(self.path)}

    def export(self) -> str:
        return json.dumps(self.all(), ensure_ascii=False, indent=2)

    def close(self):
        self.conn.close()
