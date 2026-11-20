"""Local, citation-preserving text retrieval for Studio. Source text is data, not instructions."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid



class Knowledge:
    def __init__(self, store):
        self.store = store
        for sql in [
            'CREATE TABLE IF NOT EXISTS studio_meta (key TEXT PRIMARY KEY,value TEXT)',
            'CREATE TABLE IF NOT EXISTS studio_documents (id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,text TEXT NOT NULL,digest TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)',
            'CREATE TABLE IF NOT EXISTS studio_chunks (id INTEGER PRIMARY KEY,document_id TEXT NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,text TEXT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS studio_chunks_document ON studio_chunks(document_id)',
        ]:
            store._exec(sql)
        try:
            store._exec('CREATE VIRTUAL TABLE IF NOT EXISTS studio_fts USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, title, body)')
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
    def put(self, title: str, text: str, source: str = 'User document', document_id: str | None = None) -> dict:
        if not isinstance(title, str) or not title.strip() or len(title)>160:
            raise ValueError('Give the document a title of up to 160 characters.')
        if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>2_000_000 or '\x00' in text:
            raise ValueError('Import nonempty UTF-8 text up to 2 MB; binary documents are not supported.')
        if not isinstance(source,str) or len(source)>300:
            raise ValueError('Source labels must be at most 300 characters.')
        document_id = document_id or 'doc-'+uuid.uuid4().hex[:16]
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',document_id):
            raise ValueError('Invalid document id.')
        now=time.time();digest=hashlib.sha256(text.encode()).hexdigest()
        chunks=[];lines=text.splitlines() or [text];start=0
        while start<len(lines):
            end=start;size=0
            while end<len(lines) and (size<1800 or end==start):
                size+=len(lines[end])+1;end+=1
            body='\n'.join(lines[start:end])
            # Very long single lines are split too; line references still identify the source line.
            for offset in range(0,max(1,len(body)),2400):
                chunks.append((start+1,end,body[offset:offset+2400]))
            start=end
        with self.store.transaction() as db:
            count=db.execute('SELECT COUNT(*) FROM studio_documents WHERE id<>?',(document_id,)).fetchone()[0]
            total=db.execute('SELECT COALESCE(SUM(length(text)),0) FROM studio_documents WHERE id<>?',(document_id,)).fetchone()[0]
            if count>=200 or total+len(text)>20_000_000:
                raise ValueError('The local library supports up to 200 documents and 20 million text characters.')
            db.execute('INSERT INTO studio_documents VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,source=excluded.source,text=excluded.text,digest=excluded.digest,updated=excluded.updated',
                       (document_id,title.strip(),source,text,digest,now,now))
            db.execute('DELETE FROM studio_chunks WHERE document_id=?',(document_id,))
            if self.fts:db.execute('DELETE FROM studio_fts WHERE document_id=?',(document_id,))
            for first,last,body in chunks:
                cur=db.execute('INSERT INTO studio_chunks(document_id,start_line,end_line,text) VALUES(?,?,?,?)',(document_id,first,last,body))
                if self.fts:db.execute('INSERT INTO studio_fts VALUES(?,?,?,?)',(cur.lastrowid,document_id,title,body))
        return self.get(document_id,include_text=False)

    def list(self):
        return [dict(r) for r in self.store._exec('SELECT id,title,source,digest,created,updated,length(text) AS characters FROM studio_documents ORDER BY updated DESC')]

    def get(self, document_id, include_text=True):
        fields='*' if include_text else 'id,title,source,digest,created,updated,length(text) AS characters'
        rows=self.store._exec(f'SELECT {fields} FROM studio_documents WHERE id=?',(document_id,))
        if not rows:raise ValueError('Document not found.')
        return dict(rows[0])

    def delete(self, document_id):
        self.get(document_id)
        with self.store.transaction() as db:
            if self.fts:db.execute('DELETE FROM studio_fts WHERE document_id=?',(document_id,))
            db.execute('DELETE FROM studio_chunks WHERE document_id=?',(document_id,))
            db.execute('DELETE FROM studio_documents WHERE id=?',(document_id,))

    def search(self, query: str, limit=6, documents=None):
        if not isinstance(query,str) or len(query)>2000:raise ValueError('Search queries must be text up to 2000 characters.')
        if not isinstance(limit,int) or not 1<=limit<=10:raise ValueError('Choose between 1 and 10 excerpts.')
        terms=list(dict.fromkeys(re.findall(r'[^\W_]{2,}',query.casefold())))[:24]
        if not terms:return []
        documents=documents or []
        if not isinstance(documents,list) or len(documents)>200 or any(not isinstance(d,str) for d in documents):raise ValueError('Invalid document filter.')
        filters=' AND c.document_id IN ('+','.join('?' for _ in documents)+')' if documents else ''
        if self.fts:
            match=' OR '.join('"'+term+'"' for term in terms)
            rows=self.store._exec('SELECT c.*,d.title,d.source,bm25(studio_fts) AS rank FROM studio_fts JOIN studio_chunks c ON c.id=studio_fts.chunk_id JOIN studio_documents d ON d.id=c.document_id WHERE studio_fts MATCH ?'+filters+' ORDER BY rank,c.id LIMIT ?',tuple([match]+documents+[limit]))
        else:
            clause=' OR '.join('lower(c.text || d.title) LIKE ?' for _ in terms)
            rows=self.store._exec('SELECT c.*,d.title,d.source,0 AS rank FROM studio_chunks c JOIN studio_documents d ON d.id=c.document_id WHERE ('+clause+')'+filters+' LIMIT ?',tuple(['%'+t+'%' for t in terms]+documents+[limit]))
        return [dict(r,citation=f'S{i+1}') for i,r in enumerate(rows)]

    @staticmethod
    def format(results):
        if not results:return 'No matching knowledge excerpts were found.'
        return '\n\n'.join(f"[{r['citation']}] {r['title']} — {r['source']}, lines {r['start_line']}-{r['end_line']} (document {r['document_id']})\n{r['text']}" for r in results)
