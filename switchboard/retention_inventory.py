"""Content-free, read-only inventory. Never initialize or migrate legacy stores."""
import os
import sqlite3
import stat
from pathlib import Path

TABLES = ("tasks", "usage", "laith_chats", "laith_turns", "laith_memories", "laith_profile",
          "laith_reminders", "studio_agents", "studio_blueprints", "studio_goals", "studio_runs",
          "studio_documents", "studio_chunks", "studio_fts", "capability_assets", "capability_jobs",
          "capability_meetings", "capability_voice", "executions")


def database_counts(path):
    if not path.exists(): return {"state": "absent", "tables": {}}
    if path.is_symlink() or not path.is_file(): return {"state": "not_inspected", "tables": {}}
    try:
        # Do not use immutable=1: it would ignore a live WAL. Query-only mode,
        # readonly connection and a deadline; never SELECT content columns.
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.execute("PRAGMA query_only=ON")
            import time
            deadline = time.monotonic() + 1
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                      for name in TABLES if name in existing}
            return {"state": "read_only", "tables": counts}
        finally: db.close()
    except (OSError, sqlite3.Error): return {"state": "unavailable", "tables": {}}


def file_counts(path, limit=2000):
    result = {"state": "absent", "files": 0, "bytes": 0, "symlinks_skipped": 0, "truncated": False}
    if path.is_symlink(): return {**result, "state": "not_inspected", "symlinks_skipped": 1}
    if not path.is_dir(): return result
    result["state"] = "metadata_only"
    pending, seen = [], 0
    try:
        if not hasattr(os,"O_NOFOLLOW") or not hasattr(os,"O_DIRECTORY"):
            return {**result,"state":"not_inspected"}
        pending.append(os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW))
        while pending:
            fd=pending.pop()
            try:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        seen += 1
                        if seen > limit:
                            result["truncated"] = True
                            return result
                        item = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(item.st_mode): result["symlinks_skipped"] += 1
                        elif stat.S_ISDIR(item.st_mode):
                            pending.append(os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd))
                        elif stat.S_ISREG(item.st_mode):
                            result["files"] += 1; result["bytes"] += item.st_size
            finally: os.close(fd)
    except OSError: result["state"] = "partial"
    finally:
        for fd in pending: os.close(fd)
    return result


def legacy_inventory(home, output):
    home, output = Path(home).absolute(), Path(output).absolute()
    return {"mode": "read_only_inventory", "deletion_performed": False,
            "legacy_database": database_counts(home / "data/switchboard.db"),
            "agent_execution_database": database_counts(home / "data/capability-executions.db"),
            "output_tree": file_counts(output), "capability_inputs": file_counts(home / "data/capability-inputs"),
            "data_tree_including_logs": file_counts(home / "data"),
            "limitations": ["counts_only_no_content_or_filenames", "not_an_ownership_or_lineage_map",
                "live_inventory_not_atomic", "external_agents_os_temporary_files_and_backups_not_enumerated",
                "unregistered_legacy_data_is_not_deleted_by_the_managed_vault"]}
