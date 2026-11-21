"""Persistent deduplication for agent-side file processing."""
import hashlib,json,re,sqlite3,time
from .adapters import local_execute

async def execute(config,payload):
    key=payload.get('execution_id','')
    if not re.fullmatch(r'cap-[a-f0-9]{32}',key) or payload.get('task') not in ('document.extract','document.ocr','media.convert'):raise ValueError('Invalid machine capability request.')
    encoded=json.dumps(payload,sort_keys=True,allow_nan=False)
    if len(encoded)>30_000_000:raise ValueError('Machine request is too large.')
    digest=hashlib.sha256(encoded.encode()).hexdigest();path=config.db_path.parent/'capability-executions.db'
    db=sqlite3.connect(path,timeout=10,isolation_level=None);path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,digest TEXT,status TEXT,result TEXT)')
    try:
        db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT digest,status,result FROM executions WHERE id=?',(key,)).fetchone()
        if old:
            db.execute('COMMIT')
            if old[0]!=digest:raise ValueError('Execution identity was reused for different inputs.')
            if old[1]=='done':return json.loads(old[2])
            raise ValueError('This execution was already started. Inspect its outcome before retrying with a new request.')
        db.execute('INSERT INTO executions VALUES(?,?,?,NULL)',(key,digest,'running'));db.execute('COMMIT')
        try:
            result=await local_execute(payload['task'],payload.get('params',{}),payload.get('assets',[]))
            if len(json.dumps(result))>30_000_000:raise ValueError('Machine output exceeds the transport limit.')
            db.execute('UPDATE executions SET status=?,result=? WHERE id=?',('done',json.dumps(result),key));return result
        except BaseException:
            db.execute('UPDATE executions SET status=? WHERE id=?',('interrupted',key));raise
    finally:db.close()
