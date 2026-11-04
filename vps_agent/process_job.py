"""Fixed supervisor entrypoint. It never imports or interprets task code."""
import json,math,os,subprocess,sys,threading,time
from vps_agent.windows_job import attach_current_process,terminate_named_job


def main():
    request=json.loads(sys.stdin.buffer.readline(16385))
    if set(request)!={'job_name','argv','deadline'}: raise ValueError('Invalid supervisor request')
    if not math.isfinite(request['deadline']) or request['deadline']<=time.monotonic(): raise SystemExit(124)
    attach_current_process(request['job_name'])
    watchdog=threading.Timer(max(.001,request['deadline']-time.monotonic()),
                             lambda:terminate_named_job(request['job_name']))
    watchdog.daemon=True; watchdog.start()
    try:
        process=subprocess.Popen(request['argv'],stdin=subprocess.DEVNULL,
            stdout=sys.stdout.buffer,stderr=sys.stderr.buffer,close_fds=True)
        code=process.wait()
    finally: watchdog.cancel()
    raise SystemExit(code)


if __name__=='__main__':
    try: main()
    except Exception:
        sys.stderr.write('Trusted process supervisor failed before completion.\n')
        raise SystemExit(125)
