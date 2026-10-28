"""Build the standalone worker bundle; never install or start it.

Research runtime modules are derived from controller source with imports rewritten
only. The manifest records both source and packaged hashes for review/parity tests.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import zipfile


def vendor_runtime(root):
    root=Path(root)
    target=root/'vps_agent/research_runtime'
    target.mkdir(parents=True,exist_ok=True)
    (root/'vps_agent/_version.py').write_bytes((root/'app/version.py').read_bytes())
    (target/'__init__.py').write_text('"""Generated secure research runtime; see runtime-manifest.json."""\n',encoding='utf-8')
    manifest=[]
    for name in ('cache','policy','retrieval','extraction'):
        source=root/f'app/services/research/{name}.py'
        raw=source.read_bytes()
        content=raw.decode('utf-8-sig').replace('app.services.research.', 'vps_agent.research_runtime.')
        output=content.encode('utf-8')
        (target/(name+'.py')).write_bytes(output)
        manifest.append({'module':name,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'packaged_sha256':hashlib.sha256(output).hexdigest()})
    (target/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def build(root,output):
    root=Path(root); output=Path(output)
    vendor_runtime(root)
    requirements='\n'.join(f'{name}=={importlib.metadata.version(name)}' for name in
        ('requests','pydantic','httpx','httpcore','beautifulsoup4'))+'\n'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((root/'vps_agent').rglob('*')):
            permitted=(path.suffix=='.py' or path==root/'vps_agent/research_runtime/runtime-manifest.json')
            if path.is_file() and '__pycache__' not in path.parts and permitted:
                bundle.write(path,path.relative_to(root).as_posix())
        bundle.writestr('requirements.txt',requirements)
        bundle.writestr('README.txt','Standalone worker development bundle. Install requirements in an isolated environment.\n'
            'Retrieval is disabled by default. Both server policy and QC_WORKER_ALLOW_WEB_FETCH must permit it.\n'
            'Scripts default off. Reviewed library scripts require online one-shot controller authorization and OS isolation.\n'
            'Generated code remains held; this package does not provision a build/test backend.\n'
            'Process-tree control is verified on Windows only; Linux/VPS script execution remains held pending a verified cgroup backend.\n'
            'No credentials, controller signing keys or deployment authorization are included.\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    build(Path(__file__).resolve().parents[1],args.output)
