import sys, os, socket, threading
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
root=Path('work/qwen-candidate').resolve()
pytest_temp=root.parent/('pytest-'+uuid4().hex)
sys.path.insert(0,str(root))
os.chdir(root)
os.environ['QC_ORCHESTRATOR_ENABLED']='0'
os.environ['QC_ENV']='development'
internal=threading.local()
original_connect=socket.socket.connect
original_pair=socket.socketpair
def local_pair(*args,**kwargs):
    internal.pair=True
    try: return original_pair(*args,**kwargs)
    finally: internal.pair=False
def blocked(*args,**kwargs):
    if getattr(internal,'pair',False): return original_connect(*args,**kwargs)
    raise RuntimeError('Network disabled for isolated compatibility tests')
with patch('dotenv.load_dotenv',return_value=False), patch.object(socket,'socketpair',local_pair), patch.object(socket.socket,'connect',blocked), patch.object(socket.socket,'connect_ex',blocked):
    # Hardware discovery must not escape socket isolation via `ollama list` in
    # a subprocess. This suite checks inventory shape, not the live model server.
    import vps_agent.capabilities
    vps_agent.capabilities.discover_local_models=lambda:[]
    import pytest
    selections=sys.argv[1:] or ['test_worker_authorization_enforcement.py']
    raise SystemExit(pytest.main([*[str(root/'tests'/name) for name in selections],
        '--basetemp',str(pytest_temp),'-q','--tb=short']))
