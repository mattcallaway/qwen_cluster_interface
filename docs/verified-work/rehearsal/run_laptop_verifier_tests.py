"""Opt-in fixed non-production test suite; uses existing host key authentication."""
import os,runpy,sys
import uuid
from pathlib import Path
root=Path(__file__).resolve().parent.parent
os.environ['QC_LAPTOP_VERIFIER_REHEARSAL']='1'
os.environ['QC_VERIFIER_REHEARSAL_BATCH']=uuid.uuid4().hex
(root/'outputs/laptop-verifier-latest-batch.txt').write_text(os.environ['QC_VERIFIER_REHEARSAL_BATCH'])
os.environ['QC_VERIFIER_TRANSPORT_SCRIPT']=str(root/'work/run_protected_verifier_ssh.py')
os.chdir(root)
sys.argv=['work/run_isolated_pytest.py','test_protected_verification_live.py']
runpy.run_path('work/run_isolated_pytest.py',run_name='__main__')
