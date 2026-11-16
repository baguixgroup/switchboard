"""Hub-side registry of machines (nodes) and a job queue that agents long-poll."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

ONLINE_SECONDS = 90
ACK_SECONDS = 10  # an agent must acknowledge a job this fast, or it is handed out again


class NodeOffline(RuntimeError):
    pass


class RemoteError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    node: str
    kind: str
    payload: dict
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "payload": self.payload, "created": self.created}


class NodeRegistry:
    def __init__(self, local_name: str, local_executor):
        self.local_name = local_name
        self.local_executor = local_executor
        self.nodes: dict[str, dict] = {}
        self.queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.pending: dict[str, asyncio.Future] = {}
        self.unacked: dict[str, Job] = {}
        self._timers: set[asyncio.Task] = set()

    def heartbeat(self, inventory: dict) -> None:
        inventory = dict(inventory)
        inventory["last_seen"] = time.time()
        self.nodes[inventory["node"]] = inventory

    def set_local(self, inventory: dict) -> None:
        self.heartbeat(inventory)

    def online(self, name: str) -> bool:
        if name == self.local_name:
            return True
        n = self.nodes.get(name)
        return bool(n and time.time() - n.get("last_seen", 0) < ONLINE_SECONDS)

    def list(self) -> list[dict]:
        out = []
        for name, inv in sorted(self.nodes.items()):
            d = dict(inv)
            d["online"] = self.online(name)
            d["local"] = name == self.local_name
            out.append(d)
        return out

    def mcp_locations(self) -> dict[str, list[str]]:
        """MCP server name -> nodes that run it (local first, then online agents)."""
        locs: dict[str, list[str]] = defaultdict(list)
        for name, inv in self.nodes.items():
            if not self.online(name):
                continue
            for server, st in (inv.get("mcp") or {}).items():
                if st.get("state") != "error":
                    locs[server].append(name)
        for server in locs:
            locs[server].sort(key=lambda n: n != self.local_name)
        return dict(locs)

    async def submit(self, node: str, kind: str, payload: dict, timeout: float = 600) -> dict:
        if node == self.local_name:
            return await self.local_executor.execute(kind, payload)
        if not self.online(node):
            raise NodeOffline(f"node '{node}' is offline")
        job = Job(uuid.uuid4().hex, node, kind, payload)
        fut = asyncio.get_running_loop().create_future()
        self.pending[job.id] = fut
        await self.queues[node].put(job)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(job.id, None)

    async def next_job(self, node: str, wait: float = 25) -> Job | None:
        try:
            job = await asyncio.wait_for(self.queues[node].get(), wait)
        except asyncio.TimeoutError:
            return None
        # The connection that receives this job may already be dead (agent restarted, laptop slept).
        # Hand it out again unless the agent acknowledges it quickly.
        self.unacked[job.id] = job
        t = asyncio.create_task(self._redeliver_if_unacked(job))
        self._timers.add(t)
        t.add_done_callback(self._timers.discard)
        return job

    async def _redeliver_if_unacked(self, job: Job) -> None:
        await asyncio.sleep(ACK_SECONDS)
        fut = self.pending.get(job.id)
        if self.unacked.pop(job.id, None) and fut and not fut.done():
            await self.queues[job.node].put(job)

    def ack(self, job_id: str) -> bool:
        return self.unacked.pop(job_id, None) is not None

    def complete(self, job_id: str, result: dict) -> bool:
        self.unacked.pop(job_id, None)
        fut = self.pending.get(job_id)
        if not fut or fut.done():
            return False
        if result.get("error"):
            fut.set_exception(RemoteError(result["error"]))
        else:
            fut.set_result(result.get("result"))
        return True
