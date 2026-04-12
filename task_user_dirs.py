"""Manage /data user directories in a single pass.

Order of operations (prevents conflicts between provisioning and cleanup):
  1. Identify inactive members and delete their home + /data dirs.
  2. Ensure /data/<user> exists with correct ownership for active members.
  3. Remove orphaned /data directories not belonging to any current member.
"""

import os
import pwd
import subprocess

from config import TASK_CONFIG
from utils import (
    CSML_USERS_GROUP, DATA_DIR,
    delete_directory, get_group_members, get_logger,
)

logger = get_logger(__name__)

_LASTLOG_CMD = "lastlog"


def _get_inactive_usernames(inactive_days: int) -> set:
    result = subprocess.run(
        [_LASTLOG_CMD, "-b", str(inactive_days)],
        check=True, text=True, capture_output=True,
    )
    inactive = set()
    for line in result.stdout.splitlines()[1:]:
        username = line[0:16].strip()
        if username and username != "Username":
            inactive.add(username)
    return inactive


def _lastlog_line(username: str) -> str:
    try:
        result = subprocess.run(
            [_LASTLOG_CMD, "-u", username],
            check=True, text=True, capture_output=True,
        )
        lines = result.stdout.splitlines()
        return lines[1].strip() if len(lines) >= 2 else ""
    except subprocess.CalledProcessError:
        return "(could not retrieve lastlog)"


def _provision_data_dir(username: str) -> None:
    user_dir = os.path.join(DATA_DIR, username)
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)
        #logger.info("Created directory: %s", user_dir)
    try:
        pw = pwd.getpwnam(username)
        os.chown(user_dir, pw.pw_uid, pw.pw_gid)
        os.chmod(user_dir, 0o700)
    except KeyError:
        logger.warning("User '%s' not found in system; skipping chown.", username)


def main() -> None:
    inactive_days = TASK_CONFIG["user_dirs"]["inactive_days"]
    if not os.path.exists(DATA_DIR):
        logger.error("Directory %s does not exist.", DATA_DIR)
        return

    members = get_group_members(CSML_USERS_GROUP)
    if not members:
        logger.warning("No members found in group '%s'.", CSML_USERS_GROUP)
        return

    # Resolve inactive members — log error and skip cleanup if lastlog unavailable
    try:
        inactive = _get_inactive_usernames(inactive_days)
    except FileNotFoundError:
        logger.error("`lastlog` command not found; skipping inactive user cleanup.")
        inactive = set()
    except subprocess.CalledProcessError as e:
        logger.error("Error running `lastlog`: %s; skipping inactive user cleanup.", e)
        inactive = set()

    members_set = set(members)
    inactive_members = members_set & inactive

    # Step 1: wipe home and /data contents for inactive members
    if inactive_members:
        logger.warning(
            "Cleaning %d inactive member(s) (threshold: %d days): %s",
            len(inactive_members), inactive_days, sorted(inactive_members),
        )
    for username in sorted(inactive_members):
        try:
            # Resolve home dir from passwd if available, fall back to /home/<user>
            try:
                pw = pwd.getpwnam(username)
                home_dir = pw.pw_dir
                lastlog = _lastlog_line(username)
                logger.info("  Inactive: %s (UID %d) last=%s", username, pw.pw_uid, lastlog)
            except KeyError:
                home_dir = os.path.join("/home", username)
                #logger.warning("User '%s' not in passwd; using fallback home %s", username, home_dir)

            for path in (home_dir, os.path.join(DATA_DIR, username)):
                try:
                    delete_directory(path)
                except Exception as e:
                    logger.error("Failed to delete '%s' for '%s': %s", path, username, e)
        except Exception as e:
            logger.error("Error cleaning up '%s': %s", username, e)

    # Step 2: provision /data/<user> for every member (active and inactive alike)
    logger.info("Provisioning /data dirs for %d member(s).", len(members))
    for username in sorted(members_set):
        try:
            _provision_data_dir(username)
        except Exception as e:
            logger.error("Failed to provision directory for '%s': %s", username, e)

    # Step 3: remove orphaned /data directories
    for entry in os.scandir(DATA_DIR):
        try:
            if not entry.is_dir():
                continue
            try:
                owner = pwd.getpwuid(os.stat(entry.path).st_uid).pw_name
            except Exception:
                owner = "deleted user"
            if owner.isdigit():
                logger.info("Skipping %s (numeric owner: %s)", entry.path, owner)
                continue
            if entry.name not in members_set:
                logger.info("Removing orphaned directory %s (owner: %s)", entry.path, owner)
                delete_directory(entry.path)
        except Exception as e:
            logger.error("Error processing entry %s: %s", entry.path, e)
