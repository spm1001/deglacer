#!/usr/bin/env python3
"""Sweep every CC transcript for Bash tool calls that touch session data.
Buckets: deglacer, deja, jq-on-jsonl, rg/grep-on-projects, python-on-projects, other-projects.
Pairs each tool_use with its tool_result to read errors. Writes JSONL of calls to OUT."""
import json,os,re,sys,glob,time
OUT='/tmp/dgc-sweep/calls.jsonl'
roots=[os.path.expanduser('~/.claude/projects'),os.path.expanduser('~/.claude-commis/projects')]
CMDWORD=re.compile(r'(?:^|[\s;&|(`])(deglacer|deja)(?=\s|$)')
SESSDATA=re.compile(r'\.claude(?:-commis)?/projects|\.jsonl')
def bucket(cmd):
    m=CMDWORD.search(cmd)
    if m: return m.group(1)
    if not SESSDATA.search(cmd): return None
    for k,pat in (('jq',r'(?:^|[\s|;&])jq\b'),('rg',r'(?:^|[\s|;&])(?:rg|grep|ugrep|zgrep)\b'),('python',r'python3?\b'),('tail',r'(?:^|[\s|;&])(?:tail|head|cat|wc|ls|find|stat|du)\b')):
        if re.search(pat,cmd): return k
    return 'other'
files=[]
for r in roots:
    files+=glob.glob(r+'/*/*.jsonl')+glob.glob(r+'/*/*/subagents/*.jsonl')
files.sort()
t0=time.time(); n=0; out=open(OUT,'w')
for i,f in enumerate(files):
    pending={}
    try: fh=open(f,'rb')
    except OSError: continue
    for raw in fh:
        if b'"tool_use"' in raw and (b'"Bash"' in raw) :
            try: e=json.loads(raw)
            except Exception: continue
            c=(e.get('message') or {}).get('content')
            if not isinstance(c,list): continue
            for b in c:
                if b.get('type')=='tool_use' and b.get('name')=='Bash':
                    cmd=(b.get('input') or {}).get('command','')
                    bk=bucket(cmd)
                    if bk:
                        pending[b['id']]={'file':f,'ts':e.get('timestamp'),'sid':e.get('sessionId'),'cwd':e.get('cwd'),'model':(e.get('message') or {}).get('model'),'bucket':bk,'cmd':cmd[:600],'sub':'/subagents/' in f}
        elif pending and b'tool_use_id' in raw:
            try: e=json.loads(raw)
            except Exception: continue
            c=(e.get('message') or {}).get('content')
            if not isinstance(c,list): continue
            for b in c:
                if b.get('type')=='tool_result' and b.get('tool_use_id') in pending:
                    rec=pending.pop(b['tool_use_id'])
                    rc=b.get('content'); 
                    if isinstance(rc,list): rc=' '.join(x.get('text','') for x in rc if isinstance(x,dict))
                    rec['is_error']=bool(b.get('is_error')); rec['result']=(rc or '')[:400]
                    out.write(json.dumps(rec)+'\n'); n+=1
    for rec in pending.values():
        rec['is_error']=None; rec['result']=''; out.write(json.dumps(rec)+'\n'); n+=1
    if i%500==0: print(f'{i}/{len(files)} files, {n} calls, {time.time()-t0:.0f}s',flush=True)
print('DONE',len(files),'files',n,'calls',f'{time.time()-t0:.0f}s',flush=True)
