#!/usr/bin/env python3
"""Cluster maintenance scheduler.

Runs each task in its own background thread on a fixed interval.
A task failure is logged and retried next interval without affecting other tasks.
"""

import os
import signal
import sys
import threading

import task_cpu_priority
import task_disk_quota
import task_gpu_guard
import task_resource_guard
import task_slurm_resume
import task_user_dirs
from config import TASK_CONFIG
from utils import get_logger

logger = get_logger("main")


class PeriodicTask:
    """Runs a callable periodically in a background daemon thread.

    Executes immediately on start, then waits for the next interval.
    Unhandled exceptions are logged; the thread keeps running.
    Stop events allow clean shutdown without waiting for the full interval.
    """

    def __init__(self, name: str, fn, interval_seconds: int, **kwargs):
        self.name = name
        self._fn = fn
        self._interval = interval_seconds
        self._kwargs = kwargs
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()
        logger.info("Task '%s' started (interval=%ds)", self.name, self._interval)

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while True:
            logger.info("[%s] Running", self.name)
            try:
                self._fn(**self._kwargs)
                logger.info("[%s] Done", self.name)
            except Exception:
                logger.exception("[%s] Unhandled exception — retrying next interval", self.name)

            if self._stop.wait(timeout=self._interval):
                logger.info("[%s] Stopped.", self.name)
                break


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("main.py must be run as root")

    cfg = TASK_CONFIG
    tasks: list[PeriodicTask] = []

    def register(name, fn, **extra_kwargs):
        task_cfg = cfg.get(name, {})
        if not task_cfg.get("enabled", True):
            logger.info("Task '%s' is disabled — skipping.", name)
            return
        tasks.append(PeriodicTask(name, fn, task_cfg["interval_seconds"], **extra_kwargs))

    register("cpu_priority",  task_cpu_priority.main)
    register("gpu_guard",     task_gpu_guard.main)
    register("resource_guard", task_resource_guard.main)
    register("slurm_resume",  task_slurm_resume.main)
    register("user_dirs",     task_user_dirs.main)
    register("disk_quota",    task_disk_quota.main)

    shutdown = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping all tasks...")
        for t in tasks:
            t.stop()
        shutdown.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Starting %d task(s).", len(tasks))
    for t in tasks:
        t.start()

    # Block the main thread indefinitely until a shutdown signal is received
    shutdown.wait()

    for t in tasks:
        t.join(timeout=30)

    logger.info("All tasks stopped. Exiting.")


if __name__ == "__main__":
    main()
