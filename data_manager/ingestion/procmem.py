"""
Process memory measurement and (on Windows) a job object that caps a worker's memory.

On other platforms the cap is applied with resource.RLIMIT_AS where available; measurement
falls back to resource.getrusage.
"""

import os
import sys

if os.name == "nt":
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32.GetCurrentProcess.restype = wt.HANDLE
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.CreateJobObjectW.restype = wt.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]


def self_memory_mb():
    """(current working set, peak working set, peak commit) of this process in MB."""
    if os.name == "nt":
        p = _PMC()
        p.cb = ctypes.sizeof(p)
        _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(p), p.cb)
        return round(p.WorkingSetSize / 2**20), round(p.PeakWorkingSetSize / 2**20), round(p.PeakPagefileUsage / 2**20)
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 2**20)
    return None, round(peak), None


class MemoryCap:
    """Kill-on-close job object with a commit limit (Windows). Use: cap = MemoryCap(mb); cap.assign(popen)."""

    def __init__(self, limit_mb):
        self.limit_mb = limit_mb
        self.handle = None
        if os.name == "nt":
            self.handle = _k32.CreateJobObjectW(None, None)
            info = _EXT()
            info.BasicLimitInformation.LimitFlags = 0x200 | 0x2000   # JOB_MEMORY | KILL_ON_JOB_CLOSE
            info.JobMemoryLimit = limit_mb * 2**20
            _k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def assign(self, popen):
        if self.handle:
            h = _k32.OpenProcess(0x1F0FFF, False, popen.pid)
            _k32.AssignProcessToJobObject(self.handle, h)
            _k32.CloseHandle(h)

    def peak_mb(self):
        if not self.handle:
            return None
        info = _EXT()
        _k32.QueryInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info), None)
        return round(info.PeakJobMemoryUsed / 2**20)

    def close(self):
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def posix_limit(limit_mb):
    """preexec_fn for POSIX workers."""
    def apply():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limit_mb * 2**20, limit_mb * 2**20))
        except (ImportError, ValueError, OSError):
            pass
    return apply
