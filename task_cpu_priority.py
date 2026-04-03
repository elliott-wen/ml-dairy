"""Set nice=19 + SCHED_IDLE on all non-admin user processes."""

import grp
import subprocess

import psutil

from config import TASK_CONFIG
from utils import CSML_ADMINS_GROUP, MIN_UID, get_logger

logger = get_logger(__name__)


def main() -> None:
    cfg = TASK_CONFIG["cpu_priority"]
    new_nice = cfg["new_nice"]

    try:
        admin_users = set(grp.getgrnam(CSML_ADMINS_GROUP).gr_mem)
    except KeyError:
        logger.error("Group '%s' not found; aborting task.", CSML_ADMINS_GROUP)
        return

    for proc in psutil.process_iter(["pid", "username", "uids", "nice"]):
        try:
            uid = proc.info["uids"].real
            username = proc.info["username"]
            if uid < MIN_UID or username in admin_users:
                continue
            current_nice = proc.info["nice"]
            if current_nice < new_nice:
                logger.info(
                    "Changing nice of PID %d (user %s) from %d to %d",
                    proc.pid, username, current_nice, new_nice,
                )
                proc.nice(new_nice)
                try:
                    subprocess.run(
                        ["schedtool", "-D", str(proc.pid)],
                        check=True, capture_output=True,
                    )
                except FileNotFoundError:
                    logger.warning("schedtool not found; skipping SCHED_IDLE for PID %d.", proc.pid)
                except subprocess.CalledProcessError as e:
                    logger.warning("schedtool failed for PID %d: %s", proc.pid, e)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.warning("Unexpected error on PID %s: %s", getattr(proc, "pid", "?"), e)
