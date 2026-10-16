"""
Bounded background processing for a local, single-user application.

One dispatcher thread runs at most one worker process at a time; at most `max_queued_jobs`
uploads may wait. Each worker runs in its own process under a memory cap (Windows job object
or RLIMIT_AS) and a timeout, so parsing never blocks a Flask request and a failure in a parser
cannot exhaust the machine or crash the web application.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

from .procmem import MemoryCap, posix_limit
from .registry import Registry


class QueueFull(RuntimeError):
    pass


class JobRunner:
    def __init__(self, settings, settings_overrides=None):
        self.settings = settings
        self.registry = Registry(settings)
        self.overrides = settings_overrides or {}
        self.q = queue.Queue(maxsize=settings.max_queued_jobs)
        self._thread = None
        self._lock = threading.Lock()
        self.current = None
        self.last_run = {}

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.registry.recover()
            self._thread = threading.Thread(target=self._loop, name="ingest-dispatcher", daemon=True)
            self._thread.start()

    def submit(self, job_id):
        # Mark queued first: the dispatcher may pick the job up the moment it is enqueued.
        self.registry.update_job(job_id, state="QUEUED", stage="queued", progress=0.0, error=None)
        try:
            self.q.put_nowait(job_id)
        except queue.Full:
            msg = f"{self.settings.max_queued_jobs} uploads are already waiting; try again shortly"
            self.registry.finish_job(job_id, "FAILED", stage="queued", error=msg)
            raise QueueFull(msg) from None

    def has_capacity(self):
        return not self.q.full()

    def _loop(self):
        while True:
            job_id = self.q.get()
            try:
                self.current = job_id
                self.run_one(job_id)
            except Exception as e:  # noqa: BLE001 - the dispatcher must survive
                self.registry.finish_job(job_id, "FAILED", stage="worker", error=f"dispatcher error: {e}")
            finally:
                self.current = None
                self.q.task_done()

    def run_one(self, job_id):
        s = self.settings
        env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        cmd = [sys.executable, "-m", "data_manager.ingestion.worker", job_id, self.registry.root, json.dumps(self.overrides)]
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cap = MemoryCap(s.worker_memory_limit_mb)
        kwargs = {"cwd": project_root, "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
        if os.name != "nt":
            kwargs["preexec_fn"] = posix_limit(s.worker_memory_limit_mb)
        before = self.registry.job(job_id)
        start_attempts = (before or {}).get("attempts") or 0
        t0 = time.time()
        p = subprocess.Popen(cmd, **kwargs)
        cap.assign(p)
        try:
            out, _ = p.communicate(timeout=s.worker_timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
            timed_out = True
        peak = cap.peak_mb()
        cap.close()
        self.last_run = {"job_id": job_id, "exit_code": p.returncode, "seconds": round(time.time() - t0, 1),
                         "peak_commit_mb": peak, "timed_out": timed_out}
        job = self.registry.job(job_id)
        # The worker increments `attempts` when it starts. Only this run's outcome may be judged here: if the job
        # finished and was already re-submitted (QUEUED again, attempts moved on), it belongs to the next run.
        attempts = (job or {}).get("attempts") or 0
        died_running = job and job["state"] in ("PARSING", "VALIDATING") and attempts == start_attempts + 1
        never_started = job and job["state"] == "QUEUED" and attempts == start_attempts
        if died_running or never_started:
            tail = (out or b"").decode("utf-8", "replace")[-1500:]
            if timed_out:
                err = f"worker exceeded the {s.worker_timeout_seconds} s time limit"
            elif "MemoryError" in tail or (peak and peak >= s.worker_memory_limit_mb - 32):
                err = f"worker exceeded the {s.worker_memory_limit_mb} MB memory limit"
            else:
                err = f"worker exited with code {p.returncode}: {tail.strip()[-500:]}"
            self.registry.finish_job(job_id, "FAILED", stage=job["stage"] or "parse", error=err)
        return self.last_run

    def wait_idle(self, timeout=600):
        """Test helper: block until the queue is drained."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.q.unfinished_tasks == 0:
                return True
            time.sleep(0.1)
        return False
