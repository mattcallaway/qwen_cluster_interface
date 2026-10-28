"""Isolated process harness for the real worker poll/execute/submit method.

This file is never included in the worker development bundle.
"""
import json
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse

cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
sys.path.insert(0,cfg['bundle_root'])
import httpx
from vps_agent import agent
from vps_agent.config import AgentConfig
from vps_agent.research_runtime import retrieval
from vps_agent import research_job
research_job._process_args=lambda:[sys.executable,'-I',cfg['retrieval_driver'],cfg['bundle_root'],'good',cfg['source_text']]

# The child may connect only to its assigned ephemeral test controller. Research
# HTTP responses use MockTransport. No production controller/Ollama is reachable.
port=urlparse(cfg['controller_url']).port
original_connect=socket.socket.connect
def restricted_connect(sock,address):
    if not isinstance(address,tuple) or address[0]!='127.0.0.1' or address[1]!=port:
        raise RuntimeError('Child network boundary denied connection')
    return original_connect(sock,address)
socket.socket.connect=restricted_connect
socket.socket.connect_ex=lambda *args: (_ for _ in ()).throw(RuntimeError('Child network boundary'))
original_dns=socket.getaddrinfo
socket.getaddrinfo=lambda host,port,*a,**k: (
    [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
    if str(host).endswith('.example') else original_dns(host,port,*a,**k))
retrieval.PinningHTTPTransport=lambda **kwargs:httpx.MockTransport(lambda request:
    httpx.Response(200,headers={'content-type':'text/plain'},content=cfg['source_text'].encode()))
agent.discover_local_hardware=lambda:{'hostname':'isolated-worker','os_name':'TestOS','os_version':'1',
    'architecture':'x86_64','cpu_count':2,'ram_total_mb':4096,'ram_available_mb':2048,
    'disk_total_gb':10,'disk_free_gb':5,'installed_runtimes':['python3'],
    'declared_capabilities':[],'available_models':[],'agent_version':'test'}
worker=agent.VPSWorkerAgent(AgentConfig(controller_url=cfg['controller_url'],worker_id=cfg['worker_id'],
    worker_role='general_worker',worker_token=cfg['token'],allow_web_fetch=True,data_dir=Path(cfg['data_dir'])))
worker.session.trust_env=False
assert worker.register(), 'Worker registration failed'
outcome=worker.poll_and_execute_task()
assert outcome and outcome['status']=='success', 'Worker operation failed'
assert not any(name=='app' or name.startswith('app.') for name in sys.modules), 'Controller dependency leaked into worker'
Path(cfg['result_file']).write_text(json.dumps({'status':outcome['status'],
    'result_payload':outcome['result_payload'],'controller_imported':False}),encoding='utf-8')
