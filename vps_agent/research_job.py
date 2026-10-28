"""Killable whole-operation HTTPS retrieval/extraction process.

No signing key, controller credentials or model instructions enter the child.
The fixed child entrypoint, not task data, selects the executable and operation.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import threading


class RetrievalDeadlineExceeded(TimeoutError): pass


def _process_args():
    root=str(Path(__file__).resolve().parents[1])
    return [sys.executable,'-I','-c',
        'import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_module("vps_agent.research_job",run_name="__main__")',root]


def run_bounded_retrieval(url,seconds=8):
    if not isinstance(url,str) or len(url)>2048 or not math.isfinite(float(seconds)) or not 0<float(seconds)<=8:
        raise ValueError('Invalid bounded retrieval request')
    started=time.monotonic(); deadline=started+float(seconds)
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
    process=subprocess.Popen(_process_args(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        out,err=process.communicate(json.dumps({'url':url,'seconds':seconds,'deadline_monotonic':deadline}).encode(),
            timeout=max(.001,deadline-time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=2)
        raise RetrievalDeadlineExceeded('Retrieval/extraction process exceeded its hard deadline')
    if process.returncode==124 or time.monotonic()>deadline:
        raise RetrievalDeadlineExceeded('Retrieval result arrived after the hard deadline')
    if process.returncode!=0 or len(out)>8_000_000:
        raise ValueError('Retrieval child failed or exceeded output limits')
    result=json.loads(out)
    if time.monotonic()>deadline:
        raise RetrievalDeadlineExceeded('Retrieval decoding exceeded the hard deadline')
    return result


def perform_retrieval(url,seconds):
    from vps_agent.research_runtime.retrieval import SecureFetcher
    from vps_agent.research_runtime.extraction import DocumentExtractor,detect_challenge_page
    from vps_agent.research_fetch import HttpsRetrievalPolicy
    fetched=SecureFetcher(policy=HttpsRetrievalPolicy(rate_limit_per_domain_seconds=0)).fetch(
        url,timeout_seconds=seconds,max_bytes=2_000_000)
    if not fetched.is_success or fetched.status_code!=200 or fetched.needs_browser_fallback:
        raise ValueError('Independent retrieval failed or requires browser verification')
    if fetched.content_type.split(';')[0].strip().lower() not in {'text/plain','text/html','application/xhtml+xml'}:
        raise ValueError('Unsupported retrieval content type')
    doc=DocumentExtractor().extract(fetched.content_bytes,fetched.content_type,fetched.final_url)
    if not doc.clean_text.strip() or detect_challenge_page(doc.title,doc.clean_text,
            fetched.content_bytes.decode('utf-8',errors='replace')):
        raise ValueError('Empty or challenged source')
    return {'raw_base64':base64.b64encode(fetched.content_bytes).decode('ascii'),
        'raw_sha256':hashlib.sha256(fetched.content_bytes).hexdigest(),'extracted_text':doc.clean_text,
        'extracted_sha256':hashlib.sha256(doc.clean_text.encode()).hexdigest(),
        'final_url':fetched.final_url,'redirect_chain':fetched.redirect_chain,
        'content_type':fetched.content_type,'status_code':fetched.status_code}


def main():
    request=json.loads(sys.stdin.buffer.read(16384))
    seconds=float(request['seconds'])
    if not math.isfinite(seconds) or not 0<seconds<=8: raise SystemExit(1)
    deadline=min(float(request.get('deadline_monotonic',time.monotonic()+seconds)),time.monotonic()+seconds)
    if not math.isfinite(deadline) or deadline<=time.monotonic(): raise SystemExit(124)
    # Independent fallback when the parent disappears. The parent remains the
    # stronger external kill boundary for parser/C-extension stalls holding GIL.
    watchdog=threading.Timer(deadline-time.monotonic(),lambda:os._exit(124))
    watchdog.daemon=True; watchdog.start()
    try:
        result=perform_retrieval(request['url'],request['seconds'])
        sys.stdout.write(json.dumps(result))
    except Exception as exc:
        sys.stderr.write(type(exc).__name__)
        raise SystemExit(1)
    finally: watchdog.cancel()


if __name__=='__main__': main()
