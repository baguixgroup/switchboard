"""Authenticated Laith companion endpoints; request plans are stored server-side."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    title: str = Field(default='Chat with Laith',max_length=120)


class MessageIn(BaseModel):
    message: str = Field(min_length=1,max_length=12000)
    model: str = Field(default='',max_length=160)
    ability: str = 'conversation'
    use_memory: bool = True
    use_knowledge: bool = False
    tools: str = 'none'
    billing: str = 'plans_only'
    max_cost_usd: float = 0


class SendIn(BaseModel):
    turn_id: str
    confirm: bool = False


class MemoryIn(BaseModel):
    content: str = Field(min_length=1,max_length=2000)


class StateIn(BaseModel):
    active: bool


class ArchiveIn(BaseModel):
    archived: bool = True


class ReminderIn(BaseModel):
    title: str
    due: float
    interval_hours: float = 0


class AcknowledgeIn(BaseModel):
    stop: bool = False


class GoalIn(BaseModel):
    turn_id: str
    title: str
    blueprint_id: str = 'managed-team'


def install_companion_routes(app,engine,auth):
    c=engine.companion;deps=[Depends(auth)]
    def call(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from None

    @app.get('/api/laith',dependencies=deps)
    async def overview():return {'chats':c.chats(),'archived_chats':c.chats(True),'memories':c.memories(),'abilities':c.abilities(),'pulse':c.pulse()}

    @app.get('/api/laith/pulse',dependencies=deps)
    async def pulse():return c.pulse()

    @app.post('/api/laith/chats',dependencies=deps)
    async def create(body:ChatIn):return call(c.create_chat,body.title)

    @app.get('/api/laith/chats/{key}',dependencies=deps)
    async def chat(key:str):return call(c.chat,key)

    @app.post('/api/laith/chats/{key}/archive',dependencies=deps)
    async def archive(key:str,body:ArchiveIn):return call(c.archive_chat,key,body.archived)

    @app.post('/api/laith/chats/{key}/prepare',dependencies=deps)
    async def prepare(key:str,body:MessageIn):
        try:return await c.prepare(key,**body.model_dump())
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.post('/api/laith/chats/{key}/send',dependencies=deps)
    async def send(key:str,body:SendIn):
        try:return await c.send(key,body.turn_id,body.confirm)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/laith/chats/{key}/goal',dependencies=deps)
    async def goal(key:str,body:GoalIn):return call(c.goal_from_chat,key,body.turn_id,body.title,body.blueprint_id)

    @app.post('/api/laith/memories',dependencies=deps)
    async def remember(body:MemoryIn):return call(c.remember,body.content)

    @app.post('/api/laith/memories/{key}/state',dependencies=deps)
    async def memory_state(key:str,body:StateIn):return call(c.memory_state,key,body.active)

    @app.post('/api/laith/abilities',dependencies=deps)
    async def ability(body:dict):return call(c.save_ability,body)

    @app.post('/api/laith/reminders',dependencies=deps)
    async def reminder(body:ReminderIn):return call(c.reminder,**body.model_dump())

    @app.post('/api/laith/reminders/{key}/acknowledge',dependencies=deps)
    async def acknowledge(key:str,body:AcknowledgeIn):return call(c.acknowledge,key,body.stop)
