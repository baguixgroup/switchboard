"""SQLite store: usage log, tasks, balance snapshots, sent alerts."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  source_id TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  requests INTEGER DEFAULT 1,
  cost_usd REAL DEFAULT 0,
  task_id TEXT
);
CREATE INDEX IF NOT EXISTS usage_source_ts ON usage(source_id, ts);
CREATE INDEX IF NOT EXISTS usage_provider_ts ON usage(provider, ts);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  status TEXT NOT NULL,
  prompt TEXT,
  plan TEXT,
  result TEXT,
  output_path TEXT,
  cost_usd REAL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS balances (
  provider TEXT NOT NULL,
  ts REAL NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts_sent (
  key TEXT PRIMARY KEY,
  ts REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    # ---- usage ---------------------------------------------------------------
    def record_usage(self, provider: str, model: str, source_id: str | None, input_tokens: int,
                     output_tokens: int, cost_usd: float, task_id: str | None = None,
                     requests: int = 1, ts: float | None = None) -> None:
        self._exec(
            "INSERT INTO usage(ts, provider, model, source_id, input_tokens, output_tokens, requests, cost_usd, task_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ts or time.time(), provider, model, source_id, int(input_tokens), int(output_tokens), int(requests),
             float(cost_usd), task_id),
        )

    def usage_since(self, source_id: str, since: float) -> dict[str, float]:
        row = self._exec(
            "SELECT COALESCE(SUM(cost_usd),0) usd, COALESCE(SUM(requests),0) req,"
            " COALESCE(SUM(input_tokens+output_tokens),0) tok FROM usage WHERE source_id=? AND ts>=?",
            (source_id, since),
        )[0]
        return {"usd": row["usd"], "requests": row["req"], "tokens": row["tok"]}

    def provider_spend_since(self, provider: str, since: float) -> float:
        row = self._exec("SELECT COALESCE(SUM(cost_usd),0) usd FROM usage WHERE provider=? AND ts>=?", (provider, since))[0]
        return row["usd"]

    def usage_summary(self, since: float) -> list[dict]:
        rows = self._exec(
            "SELECT provider, model, COUNT(*) calls, SUM(input_tokens) inp, SUM(output_tokens) outp, SUM(cost_usd) usd"
            " FROM usage WHERE ts>=? GROUP BY provider, model ORDER BY usd DESC",
            (since,),
        )
        return [dict(r) for r in rows]

    # ---- tasks -----------------------------------------------------------------
    def save_task(self, task_id: str, status: str, prompt: str | None = None, plan: Any = None,
                  result: str | None = None, output_path: str | None = None, cost_usd: float | None = None,
                  error: str | None = None) -> None:
        existing = self._exec("SELECT id FROM tasks WHERE id=?", (task_id,))
        if not existing:
            self._exec(
                "INSERT INTO tasks(id, ts, status, prompt, plan, result, output_path, cost_usd, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, time.time(), status, prompt, json.dumps(plan) if plan is not None else None, result,
                 output_path, cost_usd or 0, error),
            )
            return
        fields, args = ["status=?"], [status]
        for name, val in (("prompt", prompt), ("result", result), ("output_path", output_path),
                          ("cost_usd", cost_usd), ("error", error)):
            if val is not None:
                fields.append(f"{name}=?")
                args.append(val)
        if plan is not None:
            fields.append("plan=?")
            args.append(json.dumps(plan))
        args.append(task_id)
        self._exec(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", tuple(args))

    def get_task(self, task_id: str) -> dict | None:
        rows = self._exec("SELECT * FROM tasks WHERE id=?", (task_id,))
        return self._task_row(rows[0]) if rows else None

    def recent_tasks(self, limit: int = 30) -> list[dict]:
        return [self._task_row(r) for r in self._exec("SELECT * FROM tasks ORDER BY ts DESC LIMIT ?", (limit,))]

    @staticmethod
    def _task_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        if d.get("plan"):
            try:
                d["plan"] = json.loads(d["plan"])
            except ValueError:
                pass
        return d

    # ---- balances ----------------------------------------------------------------
    def save_balance(self, provider: str, data: dict) -> None:
        self._exec("INSERT INTO balances(provider, ts, data) VALUES (?,?,?)", (provider, time.time(), json.dumps(data)))

    def latest_balance(self, provider: str) -> tuple[float, dict] | None:
        rows = self._exec("SELECT ts, data FROM balances WHERE provider=? ORDER BY ts DESC LIMIT 1", (provider,))
        if not rows:
            return None
        return rows[0]["ts"], json.loads(rows[0]["data"])

    # ---- alerts --------------------------------------------------------------------
    def alert_sent(self, key: str) -> bool:
        return bool(self._exec("SELECT key FROM alerts_sent WHERE key=?", (key,)))

    def mark_alert_sent(self, key: str) -> None:
        self._exec("INSERT OR REPLACE INTO alerts_sent(key, ts) VALUES (?,?)", (key, time.time()))
