import asyncio

from switchboard import nodes
from switchboard.nodes import NodeRegistry


async def test_job_handed_to_dead_connection_is_redelivered(monkeypatch):
    monkeypatch.setattr(nodes, "ACK_SECONDS", 0.2)
    reg = NodeRegistry("hub", local_executor=None)
    reg.heartbeat({"node": "laptop"})
    submit = asyncio.create_task(reg.submit("laptop", "ping", {}, timeout=5))
    lost = await reg.next_job("laptop", wait=1)        # a stale long-poll takes it and never acks
    assert lost is not None
    again = await reg.next_job("laptop", wait=2)       # the live agent gets it again
    assert again.id == lost.id
    assert reg.ack(again.id)
    reg.complete(again.id, {"result": {"pong": True}})
    assert await submit == {"pong": True}


async def test_acked_job_is_not_redelivered(monkeypatch):
    monkeypatch.setattr(nodes, "ACK_SECONDS", 0.1)
    reg = NodeRegistry("hub", local_executor=None)
    reg.heartbeat({"node": "pc"})
    submit = asyncio.create_task(reg.submit("pc", "ping", {}, timeout=5))
    job = await reg.next_job("pc", wait=1)
    reg.ack(job.id)
    assert await reg.next_job("pc", wait=0.4) is None
    reg.complete(job.id, {"error": "boom"})
    try:
        await submit
    except nodes.RemoteError as exc:
        assert "boom" in str(exc)
