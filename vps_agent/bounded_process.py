"""Streaming output budget and Windows kernel process-tree lifetime control.

This does not authorize code, provide filesystem/network isolation, or verify
application behavior. Unsupported platforms fail closed rather than use groups
that descendants could escape. Cleanup uncertainty always prevents success.
"""
from dataclasses import dataclass
import json,math,os,subprocess,sys,threading,time
from pathlib import Path
from vps_agent.windows_job import WindowsJob,BoundaryUnavailable


MAX_CAPTURE_BYTES=65_536


@dataclass
class ProcessResult:
    args:list
    returncode:int
    stdout:str
    stderr:str
    reason:str
    cleanup_confirmed:bool
    captured_bytes:int
    output_bytes_seen:int
    elapsed_seconds:float
    boundary:str='windows_job'


def _supervisor_args():
    # The supervisor needs only stdlib. Bypass the Windows venv launcher, whose
    # own lifetime job can obscure whether our descendant cleanup was exercised.
    return [getattr(sys,'_base_executable',None) or sys.executable,'-I','-c',
        'import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_module("vps_agent.process_job",run_name="__main__")',
        str(Path(__file__).resolve().parents[1])]


def run_bounded_process(argv,*,cwd,env,timeout_seconds,cancel_event=None,output_limit=MAX_CAPTURE_BYTES):
    if (not isinstance(argv,list) or not argv or any(not isinstance(a,str) or '\x00' in a for a in argv)
            or type(output_limit) is not int or not 1<=output_limit<=MAX_CAPTURE_BYTES
            or isinstance(timeout_seconds,bool) or not math.isfinite(float(timeout_seconds))
            or not 0<float(timeout_seconds)<=30): raise ValueError('Invalid bounded process request')
    if cancel_event is not None and cancel_event.is_set():
        return ProcessResult(argv,126,'','','cancelled',True,0,0,0,boundary='not_started')
    started=time.monotonic(); deadline=started+float(timeout_seconds)
    job=WindowsJob()  # No child exists if kernel policy setup fails.
    process=None; threads=[]; buffers=[bytearray(),bytearray()]
    lock=threading.Lock(); exceeded=threading.Event(); counts={'seen':0,'stored':0}
    reason='supervisor_error'; cleanup=False; code=125
    def drain(stream,index):
        try:
            while True:
                chunk=os.read(stream.fileno(),4096)
                if not chunk: break
                with lock:
                    counts['seen']+=len(chunk)
                    keep=max(0,output_limit-counts['stored'])
                    retained=chunk[:keep]; buffers[index].extend(retained); counts['stored']+=len(retained)
                    if counts['seen']>output_limit: exceeded.set()
                if exceeded.is_set(): break
        except (OSError,ValueError): exceeded.set()
    try:
        request=json.dumps({'job_name':job.name,'argv':argv,'deadline':deadline}).encode()+b'\n'
        if len(request)>16384: raise ValueError('Supervisor request exceeds budget')
        process=subprocess.Popen(_supervisor_args(),cwd=cwd,env=env,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)
        for index,stream in enumerate((process.stdout,process.stderr)):
            thread=threading.Thread(target=drain,args=(stream,index),daemon=True); thread.start(); threads.append(thread)
        process.stdin.write(request); process.stdin.close()
        while True:
            if cancel_event is not None and cancel_event.is_set(): reason='cancelled'; break
            if exceeded.is_set(): reason='output_limit'; break
            if time.monotonic()>=deadline: reason='timeout'; break
            status=process.poll()
            if status is not None:
                code=status
                reason='orphaned_descendant' if job.active_count() else ('completed' if status!=125 else 'supervisor_error')
                break
            time.sleep(.01)
        job.terminate()
        until=time.monotonic()+2
        while job.active_count() and time.monotonic()<until: time.sleep(.01)
        cleanup=job.active_count()==0
        if process is not None:
            process.wait(timeout=max(.01,until-time.monotonic()))
        for thread in threads: thread.join(timeout=max(.01,until-time.monotonic()))
        cleanup=cleanup and all(not t.is_alive() for t in threads)
        # A final pipe read may discover over-budget output after main exit.
        if exceeded.is_set() and reason=='completed': reason='output_limit'
    except Exception:
        reason='cleanup_unverified'
        # Last-handle closure below is an independent kill fallback. Without a
        # successful accounting check, cleanup is deliberately not certified.
        cleanup=False
    finally:
        try: job.close()
        except Exception: cleanup=False
        if process is not None:
            try: process.wait(timeout=2)
            except Exception: cleanup=False
            for stream in (process.stdin,process.stdout,process.stderr):
                try: stream.close()
                except Exception: pass
    if not cleanup: reason='cleanup_unverified'
    if reason!='completed': code=124 if reason=='timeout' else 126
    return ProcessResult(argv,code,buffers[0].decode('utf-8',errors='replace'),
        buffers[1].decode('utf-8',errors='replace'),reason,cleanup,counts['stored'],counts['seen'],time.monotonic()-started)
