"""Periodically inspect running Slurm jobs and cancel any that exceed
configured limits for GPUs, CPUs, memory, or wall time.

Uses squeue for job allocation info and sstat for live resource usage.
Exempt users (e.g. admins) are never cancelled.
"""

import subprocess

from config import TASK_CONFIG
from utils import get_logger, is_user_in_group, CSML_ADMINS_GROUP

logger = get_logger(__name__)


def _parse_tres(tres: str) -> dict:
    """Parse a TRES string like 'cpu=8,mem=64G,node=1,gres/gpu:a100=2'
    into a dict of resource name → value string."""
    result = {}
    for entry in tres.split(","):
        entry = entry.strip()
        if "=" in entry:
            k, v = entry.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _squeue_gres() -> dict:
    """Return {job_id: gres_string} for all running jobs using %b (allocated GRES)."""
    try:
        result = subprocess.run(
            ["squeue", "--states=RUNNING,PENDING", "--format=%i|%b", "--noheader"],
            check=True, text=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("squeue gres fetch failed: %s", e)
        return {}
    gres_map = {}
    for line in result.stdout.strip().splitlines():
        parts = line.strip().split("|", 1)
        if len(parts) == 2:
            gres_map[parts[0].strip()] = parts[1].strip()
    return gres_map


def _parse_gres_gpus(gres: str) -> int:
    """Parse GPU count from squeue %b string like 'gres/gpu:a100:2' or 'gres/gpu:2'."""
    if not gres or gres in ("(null)", "N/A"):
        return 0
    total = 0
    for entry in gres.split(","):
        parts = entry.strip().split(":")
        if parts[0] in ("gpu", "gres/gpu") and len(parts) >= 2:
            try:
                total += int(parts[-1])
            except ValueError:
                pass
    return total


def _squeue():
    """Return all running jobs as a list of dicts.

    Uses --Format with explicit column widths for tres-alloc (CPU, memory),
    and a separate --format=%b call for GRES (GPU) since GPUs may not appear
    in tres-alloc when GPU TRES accounting is not configured.
    """
    try:
        result = subprocess.run(
            [
                "squeue",
                "--states=RUNNING,PENDING",
                "--Format=JobID:30,UserName:30,tres-alloc:200,TimeUsed:30",
                "--noheader",
            ],
            check=True, text=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("squeue failed: %s", e)
        return []

    gres_map = _squeue_gres()

    jobs = []
    for line in result.stdout.splitlines():
        if len(line) < 60:
            continue
        job_id  = line[0:30].strip()
        user    = line[30:60].strip()
        tres    = line[60:260].strip()
        elapsed = line[260:290].strip()

        if not job_id or not user:
            continue

        tres_map = _parse_tres(tres)
        gres_str = gres_map.get(job_id, "")
        gpus = _parse_gres_gpus(gres_str)

        logger.debug("job %s user=%s tres=%s gres=%s elapsed=%s",
                     job_id, user, tres, gres_str, elapsed)

        jobs.append({
            "job_id":  job_id,
            "user":    user,
            "cpus":    _parse_int(tres_map.get("cpu", "0")),
            "mem_mb":  _parse_memory(tres_map.get("mem", "0")),
            "gpus":    gpus,
            "elapsed": _parse_elapsed(elapsed),
        })
    return jobs


def _sstat_mem_mb(job_id: str) -> float:
    """Return the current RSS memory usage in MB for a running job via sstat."""
    try:
        result = subprocess.run(
            ["sstat", "--jobs", job_id, "--format=MaxRSS", "--noheader", "--parsable2"],
            check=True, text=True, capture_output=True,
        )
        total = 0.0
        for line in result.stdout.strip().splitlines():
            val = line.strip()
            if not val:
                continue
            total += _parse_memory(val)
        return total
    except subprocess.CalledProcessError:
        return 0.0


def _cancel_job(job_id: str, user: str, reason: str) -> None:
    logger.warning("Cancelling job %s (user '%s'): %s", job_id, user, reason)
    try:
        subprocess.run(["scancel", job_id], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        logger.error("scancel failed for job %s: %s", job_id, e)


def _parse_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


def _parse_memory(s: str) -> float:
    """Convert Slurm memory strings like '131072M', '128G', '1T', '4096' to MB."""
    s = s.strip()
    if not s or s == "N/A":
        return 0.0
    try:
        if s.endswith("T"):
            return float(s[:-1]) * 1024 * 1024
        if s.endswith("G"):
            return float(s[:-1]) * 1024
        if s.endswith("M") or s.endswith("m"):
            return float(s[:-1])
        if s.endswith("K") or s.endswith("k"):
            return float(s[:-1]) / 1024
        return float(s)
    except ValueError:
        return 0.0



def _parse_elapsed(s: str) -> float:
    """Convert squeue elapsed time (D-HH:MM:SS or HH:MM:SS) to minutes."""
    s = s.strip()
    try:
        days = 0
        if "-" in s:
            d, s = s.split("-", 1)
            days = int(d)
        parts = s.split(":")
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, sec = 0, int(parts[0]), int(parts[1])
        else:
            return 0.0
        return days * 1440 + h * 60 + m + sec / 60
    except (ValueError, IndexError):
        return 0.0


def main() -> None:
    cfg = TASK_CONFIG["resource_guard"]
    max_gpus     = cfg["max_gpus"]
    max_cpus     = cfg["max_cpus"]
    max_mem_mb   = cfg["max_mem_gb"] * 1024
    max_time_min = cfg["max_time_days"] * 1440

    jobs = _squeue()
    if not jobs:
        logger.info("No running jobs found.")
        return

    logger.info("Checking %d running job(s).", len(jobs))

    # --- Per-job wall-time check (not aggregated) ---
    for job in jobs:
        job_id = job["job_id"]
        user   = job["user"]
        try:
            if is_user_in_group(user, CSML_ADMINS_GROUP):
                continue
            if job["elapsed"] > max_time_min:
                _cancel_job(job_id, user,
                    f"elapsed {job['elapsed']:.0f} min exceeds limit of {max_time_min} min "
                    f"({cfg['max_time_days']} days)")
        except Exception as e:
            logger.error("Error checking wall time for job %s (user %s): %s", job_id, user, e)

    # --- Per-user aggregated resource check ---
    user_jobs: dict = {}
    for job in jobs:
        user = job["user"]
        try:
            if is_user_in_group(user, CSML_ADMINS_GROUP):
                continue
        except Exception as e:
            logger.error("Error checking admin status for user %s: %s", user, e)
            continue
        if user not in user_jobs:
            user_jobs[user] = []
        user_jobs[user].append(job)

    for user, ujobs in user_jobs.items():
        try:
            total_gpus = sum(j["gpus"] for j in ujobs)
            total_cpus = sum(j["cpus"] for j in ujobs)
            total_mem  = sum(j["mem_mb"] for j in ujobs)

            # Live RSS across all user jobs
            total_live_mem = sum(_sstat_mem_mb(j["job_id"]) for j in ujobs)

            violation = None
            if total_gpus > max_gpus:
                violation = f"total GPUs {total_gpus} exceeds limit of {max_gpus}"
            elif total_cpus > max_cpus:
                violation = f"total CPUs {total_cpus} exceeds limit of {max_cpus}"
            elif total_mem > max_mem_mb:
                violation = (f"total allocated memory {total_mem:.0f} MB "
                             f"exceeds limit of {max_mem_mb:.0f} MB")
            elif total_live_mem > max_mem_mb:
                violation = (f"total RSS {total_live_mem:.0f} MB "
                             f"exceeds limit of {max_mem_mb:.0f} MB")

            if violation:
                job_summary = ", ".join(
                    f"{j['job_id']}(gpus={j['gpus']} cpus={j['cpus']} mem={j['mem_mb']:.0f}MB)"
                    for j in ujobs
                )
                logger.warning(
                    "User '%s' exceeds limits — %s. "
                    "Totals: gpus=%d/%d cpus=%d/%d alloc_mem=%.0f/%.0fMB live_mem=%.0f/%.0fMB. "
                    "Jobs: [%s]",
                    user, violation,
                    total_gpus, max_gpus,
                    total_cpus, max_cpus,
                    total_mem, max_mem_mb,
                    total_live_mem, max_mem_mb,
                    job_summary,
                )
                for job in ujobs:
                    _cancel_job(job["job_id"], user, violation)

        except Exception as e:
            logger.error("Error checking aggregated resources for user %s: %s", user, e)
