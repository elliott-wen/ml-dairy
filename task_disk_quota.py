"""Apply XFS disk quotas for all non-system users."""

import pwd
import subprocess

from config import TASK_CONFIG
from utils import CSML_ADMINS_GROUP, MIN_UID, get_logger, is_user_in_group

logger = get_logger(__name__)


def _set_xfs_quota(user: str, mount: str, soft: str, hard: str) -> None:
    try:
        subprocess.run(
            ["sudo", "xfs_quota", "-x", "-c",
             f"limit bsoft={soft} bhard={hard} {user}", mount],
            check=True, capture_output=True,
        )
        #logger.info("Quota set for '%s' on %s (%s/%s)", user, mount, soft, hard)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to set quota for '%s' on %s: %s", user, mount, e)


def main() -> None:
    cfg = TASK_CONFIG["disk_quota"]
    mount_quotas = cfg["mount_quotas"]
    user_overrides = cfg["user_overrides"]
    admin_quota = cfg["admin_quota"]

    for p in pwd.getpwall():
        if p.pw_uid < MIN_UID:
            continue
        username = p.pw_name
        try:
            if is_user_in_group(username, CSML_ADMINS_GROUP):
                logger.info("Setting %s quota for admin '%s'", admin_quota, username)
                for mount in mount_quotas:
                    _set_xfs_quota(username, mount, admin_quota, admin_quota)
            else:
                overrides = user_overrides.get(username, {})
                for mount, (soft, hard) in mount_quotas.items():
                    if mount in overrides:
                        soft, hard = overrides[mount]
                    _set_xfs_quota(username, mount, soft, hard)
        except Exception as e:
            logger.error("Failed to process quotas for '%s': %s", username, e)

    logger.info("Quota pass complete.")
