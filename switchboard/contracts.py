"""Small, deterministic output contracts and bounded specialist work plans."""
from __future__ import annotations

import json
import re


def json_object(value):
    value=value.strip()
    if value.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',value,re.S)
        if not match:raise ValueError('Return one JSON object without surrounding commentary.')
        value=match[1]
    try:
        data=json.loads(value,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON number.')))
    except (ValueError,TypeError):raise ValueError('Return valid JSON.') from None
    if not isinstance(data,dict):raise ValueError('Return a JSON object.')
    return data


def validate_contract(contract):
    if not isinstance(contract,dict):raise ValueError('An output contract must be an object.')
    if set(contract)-{'format','min_words','max_words','required','json_fields','agents'}:raise ValueError('Unknown output check.')
    if contract.get('format','text') not in ('text','json','team_plan'):raise ValueError('Unknown output format.')
    for key in ('min_words','max_words'):
        if key in contract and (type(contract[key]) is not int or not 0<=contract[key]<=10000):raise ValueError('Word limits must be between 0 and 10,000.')
    if contract.get('max_words',10000)<contract.get('min_words',0):raise ValueError('Minimum words cannot exceed maximum words.')
    for key in ('required','json_fields','agents'):
        values=contract.get(key,[])
        if not isinstance(values,list) or len(values)>12 or any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in values):raise ValueError('Invalid output check list.')
    if contract.get('format')=='team_plan' and not contract.get('agents'):raise ValueError('A task planner needs an allowed specialist list.')
    return contract


def team_plan(output,agents):
    """Normalize a model suggestion. Models cannot select tools, budgets or models."""
    data=json_object(output)
    if set(data)-{'tasks'}:raise ValueError('A work plan contains only a tasks list.')
    tasks=data.get('tasks')
    if not isinstance(tasks,list) or not 1<=len(tasks)<=6:raise ValueError('Propose between 1 and 6 specialist tasks.')
    seen={}
    for task in tasks:
        if not isinstance(task,dict) or set(task)-{'id','agent','title','task','expected','depends_on'}:raise ValueError('A task has unsupported fields; tools and permissions cannot be delegated.')
        key=task.get('id')
        if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,23}',key) or key in seen:raise ValueError('Task ids must be unique, short identifiers.')
        if task.get('agent') not in agents:raise ValueError('Assign only an allowed specialist.')
        for field,limit in [('title',120),('task',6000),('expected',2000)]:
            value=task.get(field)
            if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:raise ValueError('Each task needs a bounded title, assignment and expected result.')
        deps=task.setdefault('depends_on',[])
        if not isinstance(deps,list) or len(deps)>6 or any(not isinstance(d,str) for d in deps) or len(set(deps))!=len(deps):raise ValueError('Invalid specialist dependencies.')
        seen[key]=task
    order=[];active=set();done=set()
    def visit(key):
        if key not in seen:raise ValueError('A specialist depends on an unknown task.')
        if key in active:raise ValueError('Specialist dependencies contain a cycle.')
        if key in done:return
        active.add(key)
        for dep in seen[key]['depends_on']:visit(dep)
        active.remove(key);done.add(key);order.append(seen[key])
    for key in seen:visit(key)
    return order


def check_output(output,contract):
    validate_contract(contract)
    words=len(output.split())
    if words<contract.get('min_words',0):raise ValueError(f'Output has {words} words; minimum is {contract["min_words"]}.')
    if words>contract.get('max_words',10000):raise ValueError(f'Output has {words} words; maximum is {contract["max_words"]}.')
    for value in contract.get('required',[]):
        if value.casefold() not in output.casefold():raise ValueError('Output must include: '+value)
    if contract.get('format') in ('json','team_plan') or contract.get('json_fields'):
        data=json_object(output)
        for field in contract.get('json_fields',[]):
            if field not in data:raise ValueError('JSON output is missing: '+field)
    if contract.get('format')=='team_plan':team_plan(output,contract['agents'])


def contract_prompt(contract):
    if not contract:return ''
    return '\n\nOutput requirements (checked before accepting this step):\n'+json.dumps(contract,ensure_ascii=False)
