"""Windows kernel process lifetime boundary, not filesystem/network containment.

References: Microsoft Learn Job Objects and JOBOBJECT_EXTENDED_LIMIT_INFORMATION.
No breakaway flags and no inheritable job handle. Unsupported/nested-job failures
raise before the reviewed target starts; there is no process-group fallback.
"""
import ctypes,os,uuid
from ctypes import wintypes as W


class BoundaryUnavailable(RuntimeError): pass


class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',W.DWORD),
        ('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),('active_limit',W.DWORD),
        ('affinity',ctypes.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
class IoCounters(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',IoCounters),('process_memory',ctypes.c_size_t),
        ('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]
class Accounting(ctypes.Structure):
    _fields_=[(name,ctypes.c_int64) for name in ('user','kernel','period_user','period_kernel')]+[
        (name,W.DWORD) for name in ('page_faults','total','active','terminated')]


def api():
    if os.name!='nt': raise BoundaryUnavailable('Verified process-tree backend unavailable on this platform')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    definitions={
        'CreateJobObjectW':([ctypes.c_void_p,W.LPCWSTR],W.HANDLE),
        'OpenJobObjectW':([W.DWORD,W.BOOL,W.LPCWSTR],W.HANDLE),
        'SetInformationJobObject':([W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD],W.BOOL),
        'QueryInformationJobObject':([W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.c_void_p],W.BOOL),
        'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
        'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),
        'CloseHandle':([W.HANDLE],W.BOOL),'GetCurrentProcess':([],W.HANDLE)}
    for name,(args,result) in definitions.items():
        fn=getattr(k,name); fn.argtypes=args; fn.restype=result
    return k


class WindowsJob:
    def __init__(self):
        self.k=api(); self.name='Local\\QwenBounded-'+uuid.uuid4().hex
        self.handle=self.k.CreateJobObjectW(None,self.name)
        if not self.handle: raise BoundaryUnavailable('CreateJobObject failed')
        limits=ExtendedLimits(); limits.basic.flags=0x2000|0x8  # kill on close + active-process ceiling
        limits.basic.active_limit=32
        if not self.k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close(); raise BoundaryUnavailable('Kernel job policy could not be set')

    def active_count(self):
        info=Accounting()
        if not self.k.QueryInformationJobObject(self.handle,1,ctypes.byref(info),ctypes.sizeof(info),None):
            raise BoundaryUnavailable('Kernel process accounting unavailable')
        return int(info.active)

    def terminate(self):
        if not self.k.TerminateJobObject(self.handle,124): raise BoundaryUnavailable('Kernel job termination failed')

    def close(self):
        if self.handle:
            handle,self.handle=self.handle,None
            if not self.k.CloseHandle(handle): raise BoundaryUnavailable('Kernel job handle close failed')


def attach_current_process(name):
    k=api(); handle=k.OpenJobObjectW(0x1|0x4,False,name)
    if not handle: raise BoundaryUnavailable('Parent-owned job is unavailable')
    try:
        if not k.AssignProcessToJobObject(handle,k.GetCurrentProcess()):
            raise BoundaryUnavailable('Supervisor could not join the kernel job')
    finally:
        # Only the parent retains a job handle. Its death kills this supervisor
        # and every child; target code is launched only after assignment/close.
        if not k.CloseHandle(handle): raise BoundaryUnavailable('Supervisor job handle close failed')


def terminate_named_job(name):
    k=api(); handle=k.OpenJobObjectW(0x8,False,name)
    if not handle: os._exit(125)
    if not k.TerminateJobObject(handle,124): os._exit(125)
    os._exit(124)
