"""Collect GPU utilisation and per-user process info, then POST to the
monitoring endpoint.  Mirrors the logic of the original gpu_status_upload.py
but integrates with the periodic task framework."""

import grp
import json
import platform
import subprocess
import time

import psutil
import requests

from config import TASK_CONFIG
from utils import get_logger

logger = get_logger(__name__)

_PROXY = "http://squid.auckland.ac.nz:3128"
_ENDPOINT = "https://ml.elliottwen.info/gpu"
_APP_ID = "4a3e71af60a8e2b364941b6b58037dca"


def _get_user_groups(username: str) -> list:
    return [g.gr_name for g in grp.getgrall() if username in g.gr_mem]


def _gpu_info() -> list:
    """Return per-GPU utilisation as a list of dicts."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,utilization.memory",
             "--format=csv,noheader"],
            check=True, text=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("nvidia-smi failed: %s", e)
        return []

    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            rows.append({
                "index":    int(parts[0].strip()),
                "util_gpu": int(parts[1].strip().rstrip("% ")),
                "util_mem": int(parts[2].strip().rstrip("% ")),
            })
        except ValueError:
            continue
    return rows


def _user_usage_info() -> dict:
    """Return per-user process and GPU bus info for all compute apps."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,gpu_bus_id,used_memory",
             "--format=csv,noheader"],
            check=True, text=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("nvidia-smi compute-apps query failed: %s", e)
        return {"pmap": {}, "bmap": {}, "gmap": {}}

    pmap, bmap, gmap = {}, {}, {}

    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        pid_str = parts[0].strip()
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        gpu_bus_id = parts[1].strip()
        used_memory = parts[2].strip()

        try:
            proc = psutil.Process(pid)
            username = proc.username()
            proc_info = proc.as_dict(attrs=[
                "pid", "name", "username", "cpu_times", "cpu_percent",
                "create_time", "cmdline", "exe", "memory_info",
                "memory_percent", "cwd",
            ])
        except Exception:
            continue

        gmap[username] = _get_user_groups(username)

        bmap.setdefault(username, []).append((gpu_bus_id, used_memory))
        pmap.setdefault(username, []).append(proc_info)

    return {"pmap": pmap, "bmap": bmap, "gmap": gmap}


def main() -> None:
    cfg = TASK_CONFIG["gpu_status_upload"]

    payload = {
        "hostname":  platform.node(),
        "timestamp": int(time.time()),
        "gpu":       _gpu_info(),
        "user":      _user_usage_info(),
    }

    proxy = cfg.get("proxy", _PROXY)
    try:
        response = requests.post(
            cfg.get("endpoint", _ENDPOINT),
            data=json.dumps(payload),
            headers={"app-id": cfg.get("app_id", _APP_ID)},
            proxies={"https": proxy, "http": proxy},
            timeout=15,
        )
        response.raise_for_status()
        logger.debug("GPU status uploaded (HTTP %d)", response.status_code)
    except requests.RequestException as e:
        logger.error("GPU status upload failed: %s", e)
