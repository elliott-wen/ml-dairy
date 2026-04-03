"""Central configuration for all cluster maintenance tasks.

Every tunable value — thresholds, intervals, quotas, toggles — lives here.
main.py reads this file; nothing is hardcoded in the task functions.
"""

TASK_CONFIG = {

    # ── CPU priority ─────────────────────────────────────────────────────────
    # Sets nice=19 + SCHED_IDLE on non-admin user processes.
    "cpu_priority": {
        "enabled":          True,
        "interval_seconds": 60,
        "new_nice":         19,     # target niceness value (max = 19)
    },

    # ── GPU / Slurm guard ────────────────────────────────────────────────────
    # Kills processes using GPU, excessive CPU, or excessive memory
    # that are not managed by Slurm.
    "gpu_guard": {
        "enabled":          True,
        "interval_seconds": 30,
        "mem_limit_gb":     16,     # RSS threshold before killing (GB)
        "cpu_limit_pct":    800,   # CPU% threshold before killing
    },

    # ── User directory management ────────────────────────────────────────────
    # Single pass: cleans inactive users, provisions active /data dirs,
    # and removes orphaned directories. Runs before disk_quota to avoid
    # setting quotas for users that are about to be removed.
    "user_dirs": {
        "enabled":          True,
        "interval_seconds": 300,
        "inactive_days":    180,  # users with no login beyond this are cleaned up
    },

    # ── Slurm job resource guard ─────────────────────────────────────────────
    # Polls running jobs and cancels any that exceed resource limits.
    # Replaces the fragile Lua job_submit hook with an async polling approach.
    "resource_guard": {
        "enabled":          True,
        "interval_seconds": 10,
        "max_gpus":         2,
        "max_cpus":         32,
        "max_mem_gb":       128,
        "max_time_days":    3,
    },

    # ── Slurm node resume ────────────────────────────────────────────────────
    # Resumes nodes stuck in draining state if slurmctld is running.
    "slurm_resume": {
        "enabled":          True,
        "interval_seconds": 60,
    },

    # ── Disk quotas ──────────────────────────────────────────────────────────
    # Applies XFS quotas per mount point. Admins get admin_quota on all mounts.
    # Per-user overrides take precedence over mount_quotas defaults.
    "disk_quota": {
        "enabled":          True,
        "interval_seconds": 300,  # every five minute
        "admin_quota":      "50t",  # soft and hard limit for CSML_admins
        "mount_quotas": {
            "/home": ("90m",  "100m"),
            "/tmp":  ("200m", "250m"),
            "/data": ("290g", "3000g"),
        },
        # Per-user overrides: {username: {mount: (soft, hard)}}
        "user_overrides": {
            "yma391": {"/data": ("1t", "1t")},
            "zwna875": {"/data": ("1t", "1t")},
            "tbai869": {"/data": ("1t", "1t")},
            "cliu797": {"/data": ("1t", "1t")},
        },
    },


}
