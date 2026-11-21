"""Bounded document conversion. Documents are data and never executable instructions."""
from __future__ import annotations
import io,json,zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

MAX_TEXT=2_000_000
class TextHTML(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag in ('p','br','div','li','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)

def extract(name,data):
    suffix=Path(name).suffix.lower();pages=[]
    if suffix=='.pdf':
        try:from pypdf import PdfReader
        except ImportError:raise ValueError('PDF support needs the documents extra on the selected machine.') from None
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted:raise ValueError('Unlock the PDF before importing it.')
        if len(reader.pages)>300:raise ValueError('Use a PDF with up to 300 pages.')
        for i,page in enumerate(reader.pages):
            text=page.extract_text() or '';pages.append({'page':i+1,'text':text})
            if sum(len(p['text']) for p in pages)>MAX_TEXT:raise ValueError('Extracted document is too large.')
        text='\n\n'.join('Page '+str(p['page'])+'\n'+p['text'] for p in pages)
        if not text.replace('Page','').strip() or not any(p['text'].strip() for p in pages):raise ValueError('This PDF has no extractable text. Use OCR for scanned pages.')
    elif suffix=='.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos=z.infolist()
            if len(infos)>2000 or sum(i.file_size for i in infos)>30_000_000:raise ValueError('Expanded document is too large.')
            raw=z.read('word/document.xml')
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('Document entities are not supported.')
            root=ElementTree.fromstring(raw);ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text='\n'.join(''.join(p.itertext()) for p in root.iter(ns+'p'))
    else:
        allowed={'.txt','.md','.rst','.csv','.json','.yaml','.yml','.html','.htm','.srt','.vtt'}
        if suffix not in allowed:raise ValueError('Choose text, Markdown, CSV, JSON, HTML, DOCX or PDF.')
        text=data.decode('utf-8-sig')
        if suffix in ('.html','.htm'):
            parser=TextHTML();parser.feed(text);text=''.join(parser.parts)
    if not text.strip() or len(text.encode())>MAX_TEXT or '\x00' in text:raise ValueError('The extracted text is empty, binary or larger than 2 MB.')
    return {'text':text,'pages':pages,'source':name}

def transcript_document(result):
    if not isinstance(result,dict) or not isinstance(result.get('text'),str):raise ValueError('Transcription did not return text.')
    text=result['text'];segments=[]
    for s in result.get('segments',[]):
        start=float(s['start']);end=float(s['end'])
        if not 0<=start<=end or end>86400:raise ValueError('Invalid transcript timestamps.')
        segments.append({'start':start,'end':end,'speaker':str(s.get('speaker',''))[:80],'text':str(s.get('text',''))[:20000]})
    if not text.strip() or len(text)>MAX_TEXT:raise ValueError('Transcription did not return usable text.')
    return {'text':text,'segments':segments,'language':str(result.get('language',''))[:30]}

def timestamp(seconds):
    ms=int(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02}.{ms:03}'

def transcript_vtt(document):
    return 'WEBVTT\n\n'+'\n\n'.join(f"{timestamp(s['start'])} --> {timestamp(s['end'])}\n{s['speaker']+': ' if s['speaker'] else ''}{s['text'].replace('-->', '→')}" for s in document['segments'])+'\n'
