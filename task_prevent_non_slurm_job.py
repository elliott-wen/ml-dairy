"""Kill processes using GPUs, excessive CPU, or excessive memory
without being managed by Slurm."""

import random
import re
import subprocess
from collections import defaultdict

import psutil

from config import TASK_CONFIG
from utils import CSML_ADMINS_GROUP, MIN_UID, get_logger, get_username, is_user_in_group

logger = get_logger("task_prevent_non_slurm_job")


def _get_slurm_managed_pids() -> set:
    try:
        result = subprocess.run(
            ["scontrol", "listpids"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        pids = set()
        for line in result.stdout.strip().splitlines():
            if line.startswith("PID") or not line.strip():
                continue
            parts = line.split()
            if parts and parts[0].isdigit():
                pids.add(int(parts[0]))
        return pids
    except Exception as e:
        logger.debug("Could not retrieve Slurm PIDs: %s", e)
        return set()


def _get_gpu_devices(proc: psutil.Process) -> set:
    try:
        result = subprocess.run(
            ["lsof", "-p", str(proc.pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        return {
            m.group(0)
            for line in result.stdout.splitlines()
            if (m := re.search(r"/dev/nvidia(\d+)", line))
        }
    except Exception:
        return set()


def _kill_proc(proc: psutil.Process, reason: str) -> None:
    try:
        user = get_username(proc.uids().real)
        logger.info("Killing PID %d (user '%s'): %s", proc.pid, user, reason)
        if random.random() < 0.5:
            proc.terminate()
    except Exception as e:
        logger.error("Failed to kill PID %d: %s", proc.pid, e)


def main() -> None:
    cfg = TASK_CONFIG["prevent_non_slurm_job"]
    mem_limit_gb = cfg["mem_limit_gb"]
    cpu_limit_pct = cfg["cpu_limit_pct"]

    slurm_pids = _get_slurm_managed_pids()
    user_gpu_usage = defaultdict(lambda: {"procs": [], "gpus": set()})
    procs_to_check_cpu = []

    for proc in psutil.process_iter(["pid", "uids", "memory_info"]):
        try:
            pid = proc.info["pid"]
            uid = proc.info["uids"].real
            username = get_username(uid)
            if uid < MIN_UID or is_user_in_group(username, CSML_ADMINS_GROUP):
                continue

            procs_to_check_cpu.append(proc)

            gpus = _get_gpu_devices(proc)
            if gpus:
                user_gpu_usage[username]["procs"].append(proc)
                user_gpu_usage[username]["gpus"].update(gpus)

            mem_info = proc.info["memory_info"]
            if mem_info is None:
                continue
            mem_gb = mem_info.rss / (1024 ** 3)
            if mem_gb > mem_limit_gb and pid not in slurm_pids:
                _kill_proc(proc, f"Memory {mem_gb:.2f} GB > {mem_limit_gb} GB, not in Slurm")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.warning("Unexpected error inspecting PID %s: %s", getattr(proc, "pid", "?"), e)

    for proc in procs_to_check_cpu:
        try:
            if proc.pid in slurm_pids:
                continue
            cpu = proc.cpu_percent(interval=0.1)
            if cpu > cpu_limit_pct:
                _kill_proc(proc, f"CPU {cpu:.1f}% > {cpu_limit_pct}%, not in Slurm")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.warning("Unexpected error checking CPU for PID %s: %s", getattr(proc, "pid", "?"), e)

    for username, info in user_gpu_usage.items():
        for proc in info["procs"]:
            try:
                if proc.pid not in slurm_pids:
                    _kill_proc(proc, "GPU job not managed by Slurm")
            except Exception as e:
                logger.warning("Error enforcing GPU policy for PID %s: %s", getattr(proc, "pid", "?"), e)
