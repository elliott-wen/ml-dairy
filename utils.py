"""Shared utilities, constants, and logging setup for all maintenance tasks."""

import logging
import os
import pwd
import grp
import shutil
import sys
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_UID = 1000
CSML_ADMINS_GROUP = "CSML_admins"
CSML_USERS_GROUP = "CSML_users"
DATA_DIR = "/data"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = "/var/log/cluster_maintain/scheduler.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

_handler_installed = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes to the shared rotating log file and stdout."""
    global _handler_installed
    logger = logging.getLogger(name)
    if not _handler_installed:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        try:
            log_dir = os.path.dirname(LOG_FILE)
            os.makedirs(log_dir, exist_ok=True)
            fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except PermissionError:
            pass  # not running as root; stdout handler below is sufficient
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        _handler_installed = True
    return logger


# ---------------------------------------------------------------------------
# User / group helpers
# ---------------------------------------------------------------------------

def is_user_in_group(username: str, groupname: str) -> bool:
    """Return True if username belongs to groupname (primary or secondary)."""
    try:
        user_groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]
        primary_gid = pwd.getpwnam(username).pw_gid
        primary_group = grp.getgrgid(primary_gid).gr_name
        return groupname in user_groups or groupname == primary_group
    except Exception:
        return False


def get_group_members(group_name: str) -> list:
    """Return all usernames in a group (primary and secondary members)."""
    members = set()
    try:
        group_info = grp.getgrnam(group_name)
        members.update(group_info.gr_mem)
        for user in pwd.getpwall():
            if user.pw_gid == group_info.gr_gid:
                members.add(user.pw_name)
    except KeyError:
        logging.getLogger(__name__).warning("Group '%s' not found.", group_name)
    return list(members)


def get_username(uid: int) -> str:
    """Resolve a UID to a username, falling back to the UID string."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def get_normal_users(min_uid: int = MIN_UID) -> dict:
    """Return {username: pwd_struct} for non-system, non-nobody users."""
    return {
        e.pw_name: e
        for e in pwd.getpwall()
        if e.pw_uid >= min_uid and e.pw_name != "nobody"
    }


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def delete_directory(path: str) -> None:
    """Delete a directory tree, logging success or failure."""
    logger = logging.getLogger(__name__)
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            logger.info("Deleted: %s", path)
        except Exception as e:
            logger.error("Failed to delete %s: %s", path, e)
    else:
        logger.warning("Path does not exist (skipped): %s", path)
