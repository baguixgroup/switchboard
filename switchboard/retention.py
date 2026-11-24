"""Opt-in registered storage and root-wide deletion; no legacy adoption or erasure.

Control/key state is intentionally separate from supported content snapshots.
All payloads, including queues and indexes, belong to one immutable root ID.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from .security import SecurityDenied, protected_mode

KINDS = ("conversation", "transcript", "log", "cache", "queue", "temporary", "evidence", "backup", "agent_result")
ID = re.compile(r"^[a-f0-9]{32}$")
log = logging.getLogger("switchboard.retention")


class RetentionFailure(ValueError):
    def __init__(self, code="retention_unavailable"):
        super().__init__(code if code in {"retention_unavailable", "retention_conflict", "retention_limit",
            "invalid_retention_input", "retention_cleanup_pending", "retention_restore_denied"} else "retention_unavailable")


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool = False
    seconds: int = 86400


def retention_policy(config):
    protected = protected_mode(config)
    raw = config.raw.get("security", {}).get("retention", {})
    if not isinstance(raw, dict) or set(raw) - {"enabled", "seconds"}:
        raise ValueError("Invalid retention policy")
    enabled, seconds = raw.get("enabled", False), raw.get("seconds", 86400)
    if type(enabled) is not bool or type(seconds) is not int or not 60 <= seconds <= 2592000:
        raise ValueError("Invalid retention policy")
    if enabled and not protected: raise ValueError("Retention requires protected mode")
    return RetentionPolicy(enabled, seconds)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class _PreviewExpiry:
    """Arm/cancel on the owner loop, acquire storage locks only in a worker."""
    def __init__(self, loop, delay, callback):
        self.loop, self.handle, self.cancelled = loop, None, False
        try: loop.call_soon_threadsafe(self.install, delay, callback)
        except RuntimeError: self.cancelled = True

    def install(self, delay, callback):
        if self.cancelled: return
        self.handle = self.loop.call_later(max(0, delay), self.expire, callback)
        if self.cancelled: self.handle.cancel()

    def expire(self, callback):
        if not self.cancelled:
            self.loop.create_task(asyncio.to_thread(callback))

    def cancel(self):
        self.cancelled = True
        if self.handle is not None:
            try: self.loop.call_soon_threadsafe(self.handle.cancel)
            except RuntimeError: pass


class Retention:
    def __init__(self, config, security, *, now=time.time):
        self.config, self.security, self.now = config, security, now
        self.policy = retention_policy(config)
        self.closed = False
        self.lock = threading.RLock()
        self.pending, self.timers = {}, {}
        self.db, self.data, self.folder_fd, self.lock_fd = None, None, None, None
        self.path = config.home / "data/retention-v1"
        self.key_digest = None
        if not self.policy.enabled: return
        try:
            key = config.secret("SWITCHBOARD_RETENTION_KEY")
            if not isinstance(key, str): raise ValueError()
            self.master = Fernet(key.encode())
            self.key_digest = hashlib.sha256(key.encode()).digest()
            # Trusted private namespace only. No caller-selected file paths.
            for p in (config.home, config.home / "data", self.path, self.path / "files"):
                if p.is_symlink(): raise ValueError()
            self.path.mkdir(mode=0o700, exist_ok=True)
            self.path.chmod(0o700)
            (self.path / "files").mkdir(mode=0o700, exist_ok=True)
            (self.path / "files").chmod(0o700)
            import fcntl
            self.lock_fd = os.open(self.path / "writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.folder_fd = os.open(self.path / "files", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for name in ("control.sqlite", "content.sqlite", "control.sqlite-journal", "content.sqlite-journal", "control.sqlite-wal", "content.sqlite-wal", "control.sqlite-shm", "content.sqlite-shm"):
                p = self.path / name
                if p.is_symlink() or (p.exists() and (not p.is_file() or p.stat().st_nlink != 1)): raise ValueError()
            self.db = sqlite3.connect(self.path / "control.sqlite", check_same_thread=False)
            self.data = sqlite3.connect(self.path / "content.sqlite", check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            for db in (self.db, self.data):
                db.execute("PRAGMA secure_delete=ON")
                db.execute("PRAGMA journal_mode=DELETE")
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS roots(id TEXT PRIMARY KEY,created REAL,expires REAL,state TEXT);
                CREATE TABLE IF NOT EXISTS keys(root TEXT PRIMARY KEY,wrapped BLOB);
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,root TEXT,parent TEXT,kind TEXT,storage TEXT,created REAL,expires REAL,size INTEGER,state TEXT);
                CREATE INDEX IF NOT EXISTS artifacts_root ON artifacts(root);
                CREATE TABLE IF NOT EXISTS tombstones(root TEXT PRIMARY KEY,deleted REAL);
                CREATE TABLE IF NOT EXISTS agents(root TEXT,agent TEXT,state TEXT,token_hash TEXT,PRIMARY KEY(root,agent));
                CREATE TABLE IF NOT EXISTS providers(root TEXT,provider TEXT,state TEXT,PRIMARY KEY(root,provider));
                CREATE TABLE IF NOT EXISTS vault_identity(id INTEGER PRIMARY KEY,check_value BLOB);
            ''')
            self.data.executescript('''
                CREATE TABLE IF NOT EXISTS payloads(id TEXT PRIMARY KEY,root TEXT,cipher BLOB);
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(artifact UNINDEXED,root UNINDEXED,terms);
            ''')
            check = self.db.execute("SELECT check_value FROM vault_identity WHERE id=1").fetchone()
            if check:
                if self.master.decrypt(check[0]) != b"switchboard-retention-v1": raise ValueError()
            else:
                if self.db.execute("SELECT COUNT(*) FROM roots").fetchone()[0]: raise ValueError()
                self.db.execute("INSERT INTO vault_identity VALUES(1,?)", (self.master.encrypt(b"switchboard-retention-v1"),))
                self.db.commit()
            for name in ("control.sqlite", "content.sqlite"): (self.path / name).chmod(0o600)
        except Exception:
            self.close()
            raise RetentionFailure() from None

    def guard(self, principal):
        self.security._require_parent(principal)
        self._policy_guard()

    def _policy_guard(self):
        try: current = retention_policy(self.config)
        except (ValueError, TypeError): raise SecurityDenied("retention_not_authorized") from None
        key = self.config.secret("SWITCHBOARD_RETENTION_KEY")
        if self.closed or not self.policy.enabled or not self.security.enabled or current != self.policy or not isinstance(key, str) or not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self.key_digest):
            raise SecurityDenied("retention_not_authorized")

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not ID.fullmatch(value): raise RetentionFailure("invalid_retention_input")
        return value

    def active(self, root):
        root = self.identifier(root)
        row = self.db.execute("SELECT * FROM roots WHERE id=?", (root,)).fetchone()
        if not row or row["state"] != "active" or row["expires"] <= self.now() or self.db.execute("SELECT 1 FROM tombstones WHERE root=?", (root,)).fetchone():
            raise RetentionFailure("retention_conflict")
        return row

    def cipher(self, root):
        self.active(root)
        row = self.db.execute("SELECT wrapped FROM keys WHERE root=?", (root,)).fetchone()
        if not row: raise RetentionFailure("retention_conflict")
        try: return Fernet(self.master.decrypt(row[0]))
        except Exception: raise RetentionFailure() from None

    def create(self, principal):
        with self.lock:
            self.guard(principal)
            if self.db.execute("SELECT COUNT(*) FROM roots WHERE state='active'").fetchone()[0] >= 256:
                raise RetentionFailure("retention_limit")
            root, now = secrets.token_hex(16), self.now()
            with self.db:
                self.db.execute("INSERT INTO roots VALUES(?,?,?,'active')", (root,now,now+self.policy.seconds))
                self.db.execute("INSERT INTO keys VALUES(?,?)", (root,self.master.encrypt(Fernet.generate_key())))
            return root

    def terms(self, root, text):
        self.active(root)
        row = self.db.execute("SELECT wrapped FROM keys WHERE root=?", (root,)).fetchone()
        if not row: raise RetentionFailure("retention_conflict")
        raw = hmac.new(base64.urlsafe_b64decode(self.master.decrypt(row[0])),b"switchboard-search-v1",hashlib.sha256).digest()
        # Domain-separated keyed search tokens, never plaintext FTS content.
        return [hmac.new(raw,b"search\0"+word.encode(),hashlib.sha256).hexdigest()
                for word in sorted(set(re.findall(r"[^\W_]{2,}",text.casefold())))[:256]]

    def put(self, principal, root, kind, payload, *, parent=None, storage="database"):
        with self.lock:
            self.guard(principal)
            row = self.active(root)
            if kind not in KINDS or storage not in ("database", "file") or not isinstance(payload, bytes) or not 0 < len(payload) <= (4_000_000 if kind == "backup" else 64000):
                raise RetentionFailure("invalid_retention_input")
            if parent is not None:
                self.identifier(parent)
                if not self.db.execute("SELECT 1 FROM artifacts WHERE id=? AND root=? AND state='ready' AND expires>? AND kind<>'backup'", (parent,root,self.now())).fetchone(): raise RetentionFailure("retention_conflict")
            count, size = self.db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM artifacts WHERE root=?",(root,)).fetchone()
            if count >= 64 or size+len(payload) > 8_000_000 or self.db.execute("SELECT COALESCE(SUM(size),0) FROM artifacts").fetchone()[0]+len(payload) > 32_000_000:
                raise RetentionFailure("retention_limit")
            key, cipher, now = secrets.token_hex(16), self.cipher(root), self.now()
            expiry = min(row["expires"], now+900 if kind in ("temporary", "queue", "cache", "agent_result") else row["expires"])
            if parent is not None:
                expiry = min(expiry,self.db.execute("SELECT expires FROM artifacts WHERE id=?",(parent,)).fetchone()[0])
            with self.db:
                self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?, 'staging')", (key,root,parent,kind,storage,now,expiry,len(payload)))
            blob = cipher.encrypt(payload)
            if storage == "file":
                fd = os.open(key+".bin",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.folder_fd)
                with os.fdopen(fd,"wb") as out: out.write(blob); out.flush(); os.fsync(out.fileno())
                os.fsync(self.folder_fd)
            else:
                with self.data: self.data.execute("INSERT INTO payloads VALUES(?,?,?)",(key,root,blob))
            # Only UTF-8 text receives a keyed index; binary content stays opaque.
            try: tokens = self.terms(root,payload.decode()) if kind != "backup" else []
            except UnicodeError: tokens = []
            with self.data: self.data.execute("INSERT INTO search_index VALUES(?,?,?)",(key,root," ".join(tokens)))
            self.guard(principal); self.active(root)
            with self.db: self.db.execute("UPDATE artifacts SET state='ready' WHERE id=?",(key,))
            return key

    def _blob(self, row):
        if row["storage"] == "database":
            value = self.data.execute("SELECT cipher FROM payloads WHERE id=? AND root=?",(row["id"],row["root"])).fetchone()
            if not value: raise RetentionFailure()
            return value[0]
        fd = os.open(row["id"]+".bin",os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self.folder_fd)
        with os.fdopen(fd,"rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_nlink != 1: raise RetentionFailure()
            value = stream.read(5_400_001)
            if len(value) > 5_400_000: raise RetentionFailure("retention_limit")
            return value

    def read(self, principal, root, artifact):
        with self.lock:
            self.guard(principal); self.active(root); self.identifier(artifact)
            row = self.db.execute("SELECT * FROM artifacts WHERE id=? AND root=? AND state='ready'",(artifact,root)).fetchone()
            if not row or row["expires"] <= self.now(): raise RetentionFailure("retention_conflict")
            try: value = self.cipher(root).decrypt(self._blob(row))
            except RetentionFailure: raise
            except Exception: raise RetentionFailure() from None
            self.guard(principal); self.active(root)
            if row["expires"] <= self.now(): raise RetentionFailure("retention_conflict")
            return value

    def search(self, principal, root, query):
        with self.lock:
            self.guard(principal); self.active(root)
            if not isinstance(query,str) or not 0 < len(query) <= 1000: raise RetentionFailure("invalid_retention_input")
            terms = self.terms(root,query)
            if not terms: return []
            ids = [r[0] for r in self.data.execute("SELECT artifact FROM search_index WHERE search_index MATCH ? AND root=? LIMIT 64",(" OR ".join(terms),root))]
            return [i for i in ids if self.db.execute("SELECT 1 FROM artifacts WHERE id=? AND root=? AND state='ready' AND expires>?",(i,root,self.now())).fetchone()]

    def _plan(self, root):
        self.identifier(root)
        row = self.db.execute("SELECT * FROM roots WHERE id=?",(root,)).fetchone()
        if not row: raise RetentionFailure("retention_conflict")
        return {"root":root,"state":row["state"],"expires":row["expires"],
                "artifacts":[dict(r) for r in self.db.execute("SELECT id,parent,kind,storage,expires,size,state FROM artifacts WHERE root=? ORDER BY id",(root,))],
                "agents":[dict(r) for r in self.db.execute("SELECT agent,state FROM agents WHERE root=? ORDER BY agent",(root,))],
                "providers":[dict(r) for r in self.db.execute("SELECT provider,state FROM providers WHERE root=? ORDER BY provider",(root,))]}

    def forget(self, key):
        with self.lock:
            self.pending.pop(key,None)
            timer=self.timers.pop(key,None)
            if timer: timer.cancel()

    async def preview_delete(self, principal, root):
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._prepare_preview, principal, root, asyncio.get_running_loop(), cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except SecurityDenied:
            raise
        except (OSError, sqlite3.Error):
            raise RetentionFailure() from None

    def _prepare_preview(self, principal, root, loop, cancelled):
        with self.lock:
            self.guard(principal)
            if cancelled.is_set(): raise SecurityDenied("deletion_not_authorized")
            for key,item in list(self.pending.items()):
                if item[3] <= self.now(): self.forget(key)
            if len(self.pending) >= 16: raise RetentionFailure("retention_limit")
            plan=self._plan(root); digest=hashlib.sha256(encoded(plan)).hexdigest(); key=secrets.token_hex(16)
            expires = self.now()+120
            self.pending[key]=(principal,root,digest,expires)
            self.timers[key]=_PreviewExpiry(loop,120,lambda:self.forget(key))
            if cancelled.is_set():
                self.forget(key)
                raise SecurityDenied("deletion_not_authorized")
            return {"preview_id":key,"digest":digest,"expires":expires,"plan":plan,
                    "scope":"registered_root_only","legacy_data_deleted":False,"provider_deletion_guaranteed":False}

    def confirm_delete(self, principal, preview_id, digest, confirm):
        with self.lock:
            self.guard(principal)
            item=self.pending.get(preview_id)
            if not item or item[0] != principal or item[3] <= self.now() or confirm is not True or not isinstance(digest,str) or not hmac.compare_digest(item[2],digest):
                raise SecurityDenied("deletion_not_authorized")
            self.forget(preview_id)
            if hashlib.sha256(encoded(self._plan(item[1]))).hexdigest() != digest:
                raise RetentionFailure("retention_conflict")
            self._tombstone(item[1])
            self._cleanup(item[1])
            return self.receipt(principal,item[1])

    def _tombstone(self, root):
        # Commit denial and destroy the locally wrapped data key BEFORE touching
        # content/files. Any crash afterward must remain inaccessible/retryable.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tombstones VALUES(?,?)",(root,self.now()))
            self.db.execute("UPDATE roots SET state='deleting' WHERE id=?",(root,))
            self.db.execute("DELETE FROM keys WHERE root=?",(root,))
            self.db.execute("UPDATE agents SET state='pending' WHERE root=? AND state='registered'",(root,))

    def _remove(self, row):
        if row["storage"] == "file":
            try:
                info=os.stat(row["id"]+".bin",dir_fd=self.folder_fd,follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise RetentionFailure("retention_cleanup_pending")
                os.unlink(row["id"]+".bin",dir_fd=self.folder_fd); os.fsync(self.folder_fd)
            except FileNotFoundError: pass
        with self.data:
            self.data.execute("DELETE FROM search_index WHERE artifact=?",(row["id"],))
            self.data.execute("DELETE FROM payloads WHERE id=?",(row["id"],))
        with self.db: self.db.execute("DELETE FROM artifacts WHERE id=?",(row["id"],))

    def _cleanup(self, root):
        for row in self.db.execute("SELECT * FROM artifacts WHERE root=?",(root,)).fetchall():
            try: self._remove(row)
            except (OSError,RetentionFailure,sqlite3.Error): return False
        # Also remove content attributed to this root if its registry row was
        # lost or content-only recovery left an orphan. Never sweep other roots.
        with self.data:
            self.data.execute("DELETE FROM search_index WHERE root=?",(root,))
            self.data.execute("DELETE FROM payloads WHERE root=?",(root,))
        with self.db: self.db.execute("UPDATE roots SET state='deleted' WHERE id=?",(root,))
        return True

    def maintain(self, principal):
        with self.lock:
            self.guard(principal)
            self._maintain()
            roots=self.db.execute("SELECT COUNT(*) FROM roots WHERE state='deleting'").fetchone()[0]
            artifacts=self.db.execute("SELECT COUNT(*) FROM artifacts WHERE state='staging' OR expires<=?",(self.now(),)).fetchone()[0]
            return {"maintenance_complete":not bool(roots or artifacts),"cleanup_pending_roots":roots,
                    "expired_or_staging_artifacts_pending":artifacts,"scope":"registered_vault_only"}

    def _maintain(self):
        for row in self.db.execute("SELECT id FROM roots WHERE state='active' AND expires<=?",(self.now(),)).fetchall(): self._tombstone(row[0])
        for row in self.db.execute("SELECT id FROM roots WHERE state='deleting'").fetchall(): self._cleanup(row[0])
        for row in self.db.execute("SELECT a.* FROM artifacts a JOIN roots r ON r.id=a.root WHERE r.state='active' AND (a.expires<=? OR a.state='staging')",(self.now(),)).fetchall():
            try: self._remove(row)
            except (OSError,RetentionFailure,sqlite3.Error): pass

    def _scheduled_maintain(self):
        with self.lock:
            try: self._policy_guard()
            except SecurityDenied: return
            for key, item in list(self.pending.items()):
                if item[3] <= self.now(): self.forget(key)
            self._maintain()

    async def run_maintenance(self):
        while not self.closed:
            try: await asyncio.to_thread(self._scheduled_maintain)
            except Exception: log.warning("retention_maintenance_pending")
            await asyncio.sleep(60)

    def receipt(self, principal, root):
        with self.lock:
            self.guard(principal); plan=self._plan(root)
            return {"root":root,"local_deleted":plan["state"]=="deleted","cleanup_pending":plan["state"]=="deleting",
                    "tombstoned":bool(self.db.execute("SELECT 1 FROM tombstones WHERE root=?",(root,)).fetchone()),
                    "agent_copies":plan["agents"],"provider_copies":plan["providers"],
                    "agent_ack_verification":"authenticated_claim_not_independently_verified",
                    "local_key_destroyed":not bool(self.db.execute("SELECT 1 FROM keys WHERE root=?",(root,)).fetchone()),
                    "physical_erasure_guaranteed":False,"provider_deletion_guaranteed":False,"legacy_data_deleted":False}

    def register_agent(self, principal, root, agent):
        """Capability contract for future managed agents; no network dispatch."""
        with self.lock:
            self.guard(principal); self.active(root); self.identifier(agent)
            if self.db.execute("SELECT COUNT(*) FROM agents WHERE root=?",(root,)).fetchone()[0] >= 16:
                raise RetentionFailure("retention_limit")
            token=secrets.token_urlsafe(32)
            with self.db:
                self.db.execute("INSERT INTO agents VALUES(?,?,'registered',?)",(root,agent,hashlib.sha256(token.encode()).hexdigest()))
            return token

    def agent_ack(self, root, agent, token):
        # Possession of a deletion-only per-copy capability cannot read/write
        # payloads, register new copies, reverse tombstones or confirm providers.
        with self.lock:
            self._policy_guard()
            self.identifier(root); self.identifier(agent)
            row=self.db.execute("SELECT state,token_hash FROM agents WHERE root=? AND agent=?",(root,agent)).fetchone()
            if not isinstance(token,str) or len(token)>128 or not row or row[0] not in ("pending","reported_deleted") or not hmac.compare_digest(row[1],hashlib.sha256(token.encode()).hexdigest()):
                raise SecurityDenied("agent_deletion_not_authorized")
            if not self.db.execute("SELECT 1 FROM tombstones WHERE root=?",(root,)).fetchone(): raise SecurityDenied("agent_deletion_not_authorized")
            with self.db: self.db.execute("UPDATE agents SET state='reported_deleted' WHERE root=? AND agent=?",(root,agent))
            return {"acknowledged":True,"verification":"authenticated_agent_claim_only","root":root}

    def register_provider(self, principal, root, provider):
        with self.lock:
            self.guard(principal); self.active(root)
            if provider not in ("openai","anthropic","gemini"): raise RetentionFailure("invalid_retention_input")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO providers VALUES(?,?,'no_request_level_deletion_adapter')",(root,provider))

    def snapshot(self, principal, root):
        with self.lock:
            self.guard(principal); self.active(root)
            items=[]
            for row in self.db.execute("SELECT * FROM artifacts WHERE root=? AND state='ready' AND kind<>'backup' AND expires>? ORDER BY id",(root,self.now())):
                item=dict(row); item.pop("root"); item.pop("state")
                item["cipher"]=self._blob(row).decode("ascii")
                items.append(item)
            value=self.cipher(root).encrypt(encoded({"version":1,"root":root,"items":items}))
            if len(value)>4_000_000: raise RetentionFailure("retention_limit")
            self.guard(principal); self.active(root)
            if any(item["expires"] <= self.now() for item in items):
                raise RetentionFailure("retention_conflict")
            return value

    def backup(self, principal, root):
        with self.lock:
            self.guard(principal); self.active(root)
            # Backups are copies too: inherit the earliest captured deadline.
            earliest=self.db.execute("SELECT id FROM artifacts WHERE root=? AND state='ready' AND kind<>'backup' AND expires>? ORDER BY expires,id LIMIT 1",(root,self.now())).fetchone()
            return self.put(principal,root,"backup",self.snapshot(principal,root),
                            parent=earliest[0] if earliest else None,storage="file")

    def restore(self, principal, root, snapshot):
        """Data-only restore against LIVE control/key/tombstone state, never rollback."""
        with self.lock:
            self.guard(principal); root_row=self.active(root)
            if not isinstance(snapshot,bytes) or len(snapshot)>4_000_000: raise RetentionFailure("retention_restore_denied")
            cipher=self.cipher(root)
            try:
                value=json.loads(cipher.decrypt(snapshot))
                if set(value)!={"version","root","items"} or type(value["version"]) is not int or value["version"]!=1 or value["root"]!=root or not isinstance(value["items"],list) or len(value["items"])>64: raise ValueError()
                items=value["items"]; identities=set(); total=0
                for item in items:
                    if set(item)!={"id","parent","kind","storage","created","expires","size","cipher"}: raise ValueError()
                    key=self.identifier(item["id"])
                    if key in identities or item["kind"] not in KINDS or item["kind"]=="backup" or item["storage"] not in ("database","file") or type(item["size"]) is not int or not 0<item["size"]<=64000: raise ValueError()
                    if type(item["created"]) not in (int,float) or type(item["expires"]) not in (int,float) or not 0<=item["created"]<item["expires"]<=root_row["expires"] or item["expires"]<=self.now(): raise ValueError()
                    if not isinstance(item["cipher"],str) or len(cipher.decrypt(item["cipher"].encode()))!=item["size"]: raise ValueError()
                    identities.add(key); total+=item["size"]
                if total>8_000_000: raise ValueError()
                completed=set()
                while len(completed)<len(items):
                    ready=[i for i in items if i["id"] not in completed and (i["parent"] is None or i["parent"] in completed)]
                    if not ready: raise ValueError()
                    completed.update(i["id"] for i in ready)
                for item in items:
                    if item["parent"]:
                        parent=next(i for i in items if i["id"]==item["parent"])
                        if item["expires"]>parent["expires"]: raise ValueError()
                    old=self.db.execute("SELECT * FROM artifacts WHERE id=?",(item["id"],)).fetchone()
                    if old and (old["root"]!=root or any(old[k]!=item[k] for k in ("parent","kind","storage","created","expires","size"))): raise ValueError()
            except Exception: raise RetentionFailure("retention_restore_denied") from None
            new=[i for i in items if not self.db.execute("SELECT 1 FROM artifacts WHERE id=?",(i["id"],)).fetchone()]
            count,size=self.db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM artifacts WHERE root=?",(root,)).fetchone()
            if count+len(new)>64 or size+sum(i["size"] for i in new)>8_000_000 or self.db.execute("SELECT COALESCE(SUM(size),0) FROM artifacts").fetchone()[0]+sum(i["size"] for i in new)>32_000_000:
                raise RetentionFailure("retention_limit")
            # A later conflict must not leave earlier restored copies behind.
            # This preflight runs under the same exclusive writer lock as apply.
            repair = set()
            for item in items:
                key=item["id"]; blob=item["cipher"].encode()
                registered=self.db.execute("SELECT state FROM artifacts WHERE id=?",(key,)).fetchone()
                if not registered or registered[0]!='ready': repair.add(key)
                if item["storage"]=="database":
                    old=self.data.execute("SELECT root,cipher FROM payloads WHERE id=?",(key,)).fetchone()
                    if old and (old[0]!=root or old[1]!=blob): raise RetentionFailure("retention_restore_denied")
                    if not old: repair.add(key)
                else:
                    try: existing=self._blob({"id":key,"root":root,"storage":"file"})
                    except FileNotFoundError: repair.add(key)
                    except (OSError,RetentionFailure): raise RetentionFailure("retention_restore_denied") from None
                    else:
                        if existing!=blob: raise RetentionFailure("retention_restore_denied")
            self.guard(principal); self.active(root)
            for item in items:
                self.guard(principal); self.active(root)
                key=item["id"]; blob=item["cipher"].encode()
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?,?,?,?, 'staging')",(key,root,item["parent"],item["kind"],item["storage"],item["created"],item["expires"],item["size"]))
                    if key in repair:
                        self.db.execute("UPDATE artifacts SET state='staging' WHERE id=?",(key,))
                if item["storage"]=="database":
                    old=self.data.execute("SELECT cipher FROM payloads WHERE id=?",(key,)).fetchone()
                    if old and old[0]!=blob: raise RetentionFailure("retention_restore_denied")
                    with self.data: self.data.execute("INSERT OR IGNORE INTO payloads VALUES(?,?,?)",(key,root,blob))
                else:
                    try: fd=os.open(key+".bin",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.folder_fd)
                    except FileExistsError:
                        row={"id":key,"root":root,"storage":"file"}
                        if self._blob(row)!=blob: raise RetentionFailure("retention_restore_denied")
                    else:
                        with os.fdopen(fd,"wb") as out: out.write(blob); out.flush(); os.fsync(out.fileno())
                        os.fsync(self.folder_fd)
                try: terms=self.terms(root,cipher.decrypt(blob).decode())
                except UnicodeError: terms=[]
                with self.data:
                    self.data.execute("DELETE FROM search_index WHERE artifact=?",(key,))
                    self.data.execute("INSERT INTO search_index VALUES(?,?,?)",(key,root," ".join(terms)))
                self.guard(principal); self.active(root)
                with self.db: self.db.execute("UPDATE artifacts SET state='ready' WHERE id=?",(key,))
            return {"restored":len(items),"tombstones_preserved":True,"keys_restored":False}

    def close(self):
        with self.lock:
            self.closed=True
            for key in list(self.pending): self.forget(key)
            for db in (self.db,self.data):
                if db: db.close()
            self.db,self.data=None,None
            for name in ("folder_fd","lock_fd"):
                fd=getattr(self,name)
                if fd is not None: os.close(fd); setattr(self,name,None)
