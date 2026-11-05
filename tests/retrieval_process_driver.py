"""Synthetic source transport for process-boundary tests; never packaged."""
import sys
import socket
import time
import os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import httpx
from vps_agent import research_job
from vps_agent.research_runtime import retrieval,extraction
mode=sys.argv[2]; body=sys.argv[3]
if len(sys.argv)>4:
    real_exit=os._exit
    def recorded_exit(code):
        Path(sys.argv[4]).write_text(str(code),encoding='utf-8')
        real_exit(code)
    os._exit=recorded_exit
original_dns=socket.getaddrinfo
socket.getaddrinfo=lambda host,port,*a,**k: (
    [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
    if str(host).endswith('.example') else original_dns(host,port,*a,**k))
socket.socket.connect=lambda *args: (_ for _ in ()).throw(RuntimeError('No real network in source fixture'))
def respond(request):
    if mode=='stall_fetch': time.sleep(60)
    if mode=='redirect_stall':
        time.sleep(.3)
        return httpx.Response(302,headers={'location':'https://terms.example/next'})
    if mode=='slow_stream':
        class Slow(httpx.SyncByteStream):
            def __iter__(self):
                for _ in range(100):
                    time.sleep(.3); yield b'x'*100
        return httpx.Response(200,headers={'content-type':'text/plain'},stream=Slow())
    return httpx.Response(200,headers={'content-type':'text/plain'},content=body.encode())
retrieval.PinningHTTPTransport=lambda **kwargs:httpx.MockTransport(respond)
if mode=='stall_extract':
    def stalled(*args,**kwargs): time.sleep(60)
    extraction.DocumentExtractor.extract=stalled
research_job.main()
