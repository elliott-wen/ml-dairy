"""Resume Slurm nodes stuck in draining state.

Checks that slurmctld is active, then resumes any nodes in 'drng' state.
"""

import subprocess

from utils import get_logger

logger = get_logger("task_slurm_watchdog")


def _is_slurmctld_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "slurmctld"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() == "active"
    except FileNotFoundError:
        logger.error("systemctl not found.")
        return False


def _get_draining_nodes() -> list:
    try:
        result = subprocess.run(
            ["sinfo", "-h", "-o", "%n %t"],
            check=True, capture_output=True, text=True,
        )
        nodes = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == "drng":
                nodes.append(parts[0])
        return nodes
    except subprocess.CalledProcessError as e:
        logger.error("sinfo failed: %s", e)
        return []


def main() -> None:
    if not _is_slurmctld_active():
        logger.warning("slurmctld is not running; skipping.")
        return

    logger.info("slurmctld is running.")

    draining = _get_draining_nodes()
    if not draining:
        logger.info("No nodes in draining state.")
        return

    logger.info("Resuming %d draining node(s): %s", len(draining), draining)
    for node in draining:
        try:
            subprocess.run(
                ["scontrol", "update", f"NodeName={node}", "State=RESUME"],
                check=True, capture_output=True,
            )
            logger.info("Resumed node: %s", node)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to resume node '%s': %s", node, e)
