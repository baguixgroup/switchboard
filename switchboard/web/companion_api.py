"""Authenticated Laith companion endpoints; request plans are stored server-side."""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from ..personalization import (ProfilePatch, PreferenceIn, RevisionIn, AppearanceIn, DecisionIn, ProfileConflict, RoleLookIn, StyleIn)


class ChatIn(BaseModel):
    title: str = Field(default='Chat with Laith',max_length=120)


class MessageIn(BaseModel):
    message: str = Field(min_length=1,max_length=12000)
    model: str = Field(default='',max_length=160)
    effort: Literal['','low','medium','high','xhigh','max'] | None = ''
    ability: str = 'conversation'
    use_memory: bool = True
    use_knowledge: bool = False
    tools: str = 'none'
    billing: str = 'plans_only'
    max_cost_usd: float = 0


class PreviewIn(MessageIn):
    chat_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list,max_length=4)


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
        except ProfileConflict as exc:raise HTTPException(409,str(exc)) from None
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from None

    @app.get('/api/laith/personalization',dependencies=deps)
    async def personalization():return c.personalization.overview()

    @app.post('/api/laith/personalization',dependencies=deps)
    async def update_personalization(body:ProfilePatch):return call(c.personalization.update,body)

    @app.post('/api/laith/personalization/preferences',dependencies=deps)
    async def preference(body:PreferenceIn):return call(c.personalization.set_preference,body)

    @app.post('/api/laith/personalization/preferences/{key}/delete',dependencies=deps)
    async def delete_preference(key:str,body:RevisionIn):return call(c.personalization.delete_preference,key,body.revision)

    @app.post('/api/laith/personalization/appearance',dependencies=deps)
    async def appearance(body:AppearanceIn):return call(c.personalization.request_appearance,body)

    @app.post('/api/laith/personalization/role',dependencies=deps)
    async def role_look(body:RoleLookIn):return call(c.personalization.apply_role,body)

    @app.post('/api/laith/personalization/style',dependencies=deps)
    async def personality_style(body:StyleIn):return call(c.personalization.apply_style,body)

    @app.post('/api/laith/personalization/appearance/preview',dependencies=deps)
    async def appearance_preview(body:AppearanceIn):
        from ..personalization import presentation
        return presentation(c.personalization.read(),body.appearance.model_dump())

    @app.post('/api/laith/personalization/suggestions/{key}/decision',dependencies=deps)
    async def decision(key:str,body:DecisionIn):return call(c.personalization.decide,key,body)

    @app.post('/api/laith/personalization/reset',dependencies=deps)
    async def reset(body:RevisionIn):return call(c.personalization.reset,body.revision)

    @app.get('/api/local/laith-desktop',dependencies=deps)
    async def desktop_status():
        import asyncio
        from ..desktop_pet import status
        return await asyncio.to_thread(status,engine.config)

    @app.post('/api/local/laith-desktop',dependencies=deps)
    async def desktop_action(body:dict):
        import asyncio
        from ..desktop_pet import control
        if body.get('action') not in ('show','hide','panel','pause','resume'):raise HTTPException(400,'Unknown desktop companion action.')
        try:return await asyncio.to_thread(control,engine.config,body['action'])
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.get('/api/laith',dependencies=deps)
    async def overview():return {'chats':c.chats(),'archived_chats':c.chats(True),'memories':c.memories(),'abilities':c.abilities(),'pulse':c.pulse(),'personalization':c.personalization.overview()}

    @app.get('/api/laith/pulse',dependencies=deps)
    async def pulse():return c.pulse()

    @app.post('/api/laith/chats',dependencies=deps)
    async def create(body:ChatIn):return call(c.create_chat,body.title)

    @app.get('/api/laith/chats/{key}',dependencies=deps)
    async def chat(key:str):return call(c.chat,key)

    @app.post('/api/laith/chats/{key}/archive',dependencies=deps)
    async def archive(key:str,body:ArchiveIn):return call(c.archive_chat,key,body.archived)

    @app.post('/api/laith/preview',dependencies=deps)
    async def preview(body:PreviewIn):
        if body.chat_id:call(c._chat,body.chat_id)
        hub=engine.capabilities
        assets=[call(hub.asset,key)[0] for key in body.asset_ids]
        task=hub.select_task(body.message,assets)
        if task!='text.generate':
            row=next((r for r in hub.registry.rows() if r['task']==task and r['configured'] and r['status'] not in ('unavailable','setup_required')),None)
            return {'task':task,'capability':row,'setup_required':row is None}
        try:return await c.prepare(body.chat_id,**body.model_dump(exclude={'chat_id','asset_ids'}),preview=True)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

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

    @app.post('/api/laith/chats/{key}/retry',dependencies=deps)
    async def retry(key:str,body:SendIn):
        try:return await c.retry_direct(key,body.turn_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

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
