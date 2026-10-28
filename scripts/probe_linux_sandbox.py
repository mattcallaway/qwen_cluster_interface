"""Read-only Linux readiness inventory. Does not start services or certify a backend."""
import hashlib,json,os,platform,stat,subprocess
from pathlib import Path


def probe():
    result={'schema':'qc.linux_readiness.v1','platform':platform.system(),'kernel':platform.release(),
        'backend_verified':False,'production_approved':False,'verdict':'NO_GO','mutations':[],
        'requires':['Non-production kernel enforcement tests','Approved runtime image digest',
                    'Independent verifier and native canary','Explicit production deployment approval']}
    if platform.system()!='Linux':
        result['error']='Linux host unavailable'; return result
    result['uid']=os.geteuid()
    for key,path in {'cgroup_controllers':'/sys/fs/cgroup/cgroup.controllers',
                     'security_modules':'/sys/kernel/security/lsm'}.items():
        try: result[key]=Path(path).read_text()[:2048].strip()
        except OSError: result[key]='UNAVAILABLE'
    try:
        process=subprocess.run(['/usr/bin/systemd','--version'],capture_output=True,timeout=3,
            env={'PATH':'/usr/bin:/bin','LANG':'C'})
        result['systemd_version']=process.stdout.decode(errors='replace').splitlines()[0][:256]
    except (OSError,subprocess.SubprocessError,IndexError): result['systemd_version']='UNAVAILABLE'
    launcher=Path('/usr/local/bin/qwen-sandbox-runner')
    try:
        info=launcher.lstat()
        result['legacy_launcher']={'regular':stat.S_ISREG(info.st_mode),'uid':info.st_uid,
            'mode':oct(stat.S_IMODE(info.st_mode)),
            'sha256':hashlib.sha256(launcher.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None}
    except OSError: result['legacy_launcher']='UNAVAILABLE'
    return result


if __name__=='__main__': print(json.dumps(probe(),indent=2))
