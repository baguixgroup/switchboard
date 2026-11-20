"""Authenticated API for Studio goals, agent profiles, knowledge and run decisions."""
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ..outputs import safe_join
from ..studio import TYPES
from pathlib import Path


class DocumentIn(BaseModel):
    title: str
    text: str
    source: str = 'User document'
    id: str | None = None


class SearchIn(BaseModel):
    query: str
    limit: int = 6
    documents: list[str] = Field(default_factory=list)


class RunIn(BaseModel):
    blueprint_id: str
    input: str


class DecisionIn(BaseModel):
    step: str
    approve: bool


class ResumeIn(BaseModel):
    confirm_retry: bool = False


def install_studio_routes(app,engine,auth):
    s=engine.studio;deps=[Depends(auth)]

    def call(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from None

    @app.get('/api/studio',dependencies=deps)
    async def overview():
        return {'goals':s.goals(),'agents':s.agents(),'blueprints':s.blueprints(),'runs':s.runs(),
                'documents':s.knowledge.list(),'projects':[],'step_types':sorted(TYPES)}

    @app.post('/api/studio/goals',dependencies=deps)
    async def save_goal(body:dict):return call(s.save_goal,body)

    @app.post('/api/studio/goals/{key}/start',dependencies=deps)
    async def start_goal(key:str):return call(s.start_goal,key)

    @app.post('/api/studio/agents',dependencies=deps)
    async def save_agent(body:dict):return call(s.save_agent,body)

    @app.post('/api/studio/blueprints',dependencies=deps)
    async def save_blueprint(body:dict):return call(s.save_blueprint,body)

    @app.get('/api/studio/blueprints/{key}',dependencies=deps)
    async def blueprint(key:str):return call(s.blueprint,key)

    @app.post('/api/studio/runs',dependencies=deps)
    async def start(body:RunIn):return call(s.start,body.blueprint_id,body.input)

    @app.get('/api/studio/runs/{key}',dependencies=deps)
    async def run(key:str):return call(s.run,key)

    @app.post('/api/studio/runs/{key}/decision',dependencies=deps)
    async def decision(key:str,body:DecisionIn):
        try:return await s.decision(key,body.step,body.approve)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/studio/runs/{key}/resume',dependencies=deps)
    async def resume(key:str,body:ResumeIn):
        try:return await s.resume(key,body.confirm_retry)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/studio/runs/{key}/stop',dependencies=deps)
    async def stop(key:str):return call(s.stop,key)

    @app.get('/api/studio/runs/{key}/file',dependencies=deps)
    async def download(key:str,path:str):
        run=call(s.run,key)
        allowed={state.get('file') for state in run['steps'].values() if state.get('status')=='done'}
        if path not in allowed:raise HTTPException(404,'No such completed output.')
        target=call(safe_join,Path(run['output_path'])/'files',path)
        if not target.is_file():raise HTTPException(404,'Output is not available on this hub.')
        return FileResponse(target,filename=target.name)

    @app.post('/api/studio/knowledge',dependencies=deps)
    async def put_document(body:DocumentIn):return call(s.knowledge.put,body.title,body.text,body.source,body.id)

    @app.get('/api/studio/knowledge/{key}',dependencies=deps)
    async def document(key:str):return call(s.knowledge.get,key)

    @app.delete('/api/studio/knowledge/{key}',dependencies=deps)
    async def delete_document(key:str):
        call(s.knowledge.delete,key);return {'ok':True}

    @app.post('/api/studio/knowledge/search',dependencies=deps)
    async def search(body:SearchIn):return {'results':call(s.knowledge.search,body.query,body.limit,body.documents)}
