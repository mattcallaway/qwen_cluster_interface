"""Reviewed synthetic web fixture; not a generated-code execution permission.

Select through the fixed script library. No server, dependencies, live tasks,
credentials, network calls or deployment commands are included.
"""
import hashlib
import json

HTML = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Collective task pilot</title>
<style>body{font:18px system-ui;max-width:760px;margin:3rem auto;padding:1rem;background:#f4f7fa;color:#172335}button{padding:.6rem;margin-right:.5rem}li{padding:1rem;margin:.5rem 0;background:white}small{display:block}</style>
</head><body><h1>Collective task pilot</h1>
<p>Synthetic development fixture. No live collective or VPS connection.</p>
<button id="all">All tasks</button><button id="blocked">Blocked tasks</button>
<p id="count" role="status"></p><ul id="tasks"></ul>
<script>
const tasks=[{name:'Build preview',status:'complete'},{name:'Verify behavior',status:'pending'},{name:'Deploy to VPS',status:'blocked'}];
function render(status){const list=document.getElementById('tasks');list.replaceChildren();
const selected=tasks.filter(t=>!status||t.status===status);
for(const task of selected){const row=document.createElement('li');row.textContent=task.name;
const state=document.createElement('small');state.textContent=task.status;row.appendChild(state);list.appendChild(row);}
document.getElementById('count').textContent=selected.length+' tasks';}
document.getElementById('all').addEventListener('click',()=>render(null));
document.getElementById('blocked').addEventListener('click',()=>render('blocked'));render(null);
</script></body></html>
'''

INDEX_SHA256 = hashlib.sha256(HTML.encode()).hexdigest()
SCRIPT = ('from pathlib import Path\nimport json,hashlib\n'
          + 'content = '+repr(HTML)+'\n'
          + 'Path("index.html").write_bytes(content.encode("utf-8"))\n'
          + 'result={"schema":"qc.web_pilot.v1","synthetic":True,"index_sha256":hashlib.sha256(content.encode()).hexdigest()}\n'
          + 'Path("result.json").write_text(json.dumps(result),encoding="utf-8")\nprint(json.dumps(result))\n')


def validate_result(payload):
    problems=[]
    if payload.get('schema')!='qc.web_pilot.v1': problems.append('wrong pilot schema')
    if payload.get('synthetic') is not True: problems.append('synthetic fixture required')
    if payload.get('index_sha256')!=INDEX_SHA256: problems.append('fixture hash mismatch')
    # Hash check only. Browser behavior is independently tested, not asserted here.
    return not problems,problems
