"""Signal only an independently verified, still-current offsite backup.

The heartbeat is a private capability, never public health or proof that a
subsequent protected change cannot invalidate the just-verified snapshot.
"""
import http.client
from contextlib import suppress
import os
import re
import ssl
import subprocess
import sys


HEARTBEAT_HOST = "sm.hetrixtools.net"
HEARTBEAT_URL = re.compile(r"https://sm\.hetrixtools\.net/hb/\?s=[A-Za-z0-9_-]{16,256}\Z")


class BackupMonitorError(ValueError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail)


def validate_heartbeat_url(value):
    if not isinstance(value, str) or (value and not HEARTBEAT_URL.fullmatch(value)):
        raise ValueError("BACKUP_HEARTBEAT_URL must be the private HTTPS HetrixTools heartbeat URL")


def _send_direct(url, *, connection_factory=None):
    """One verified TLS HEAD request, with no redirect, proxy, body or retry.

    http.client does not log request URLs by default. In particular, avoid an
    HTTP client whose normal informational request logger exposes this token.
    """
    validate_heartbeat_url(url)
    if not url:
        raise BackupMonitorError("Backup monitoring is not configured.")
    connection = None
    try:
        factory = connection_factory or http.client.HTTPSConnection
        connection = factory(HEARTBEAT_HOST, timeout=5, context=ssl.create_default_context())
        connection.set_debuglevel(0)
        connection.request("HEAD", url.removeprefix("https://" + HEARTBEAT_HOST),
                           headers={"User-Agent": "ConnectTheBots-BackupMonitor/1", "Connection": "close"})
        response = connection.getresponse()
        # Do not consume or report provider bodies, including error responses.
        if response.status != 200:
            raise BackupMonitorError("Backup monitoring did not acknowledge success; check the private monitor configuration.")
    except BackupMonitorError:
        raise
    except Exception:
        raise BackupMonitorError("Backup monitoring could not be reached; the verified backup remains saved.") from None
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()


def _send(url, *, command=None, timeout=7):
    """Bound DNS, TLS and all response headers before releasing the writer fence.

    The capability travels only through the child's private stdin pipe. Child
    output is discarded, and only interpreter/platform settings are inherited.
    subprocess.run kills AND waits for a timed-out child before returning.
    """
    validate_heartbeat_url(url)
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "PYTHONPATH", "PYTHONHOME",
    }}
    try:
        result = subprocess.run(command or [sys.executable, "-m", "connectthebots_server.backup_monitor", "--send"],
                                input=url, text=True, encoding="ascii", env=environment, timeout=timeout,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0:
            raise BackupMonitorError("Backup monitoring did not acknowledge success; check the private monitor configuration.")
    except BackupMonitorError:
        raise
    except Exception:
        raise BackupMonitorError("Backup monitoring could not be reached; the verified backup remains saved.") from None


def signal_backup_success(database, url, result, *, sender=None):
    """The CLI's success signal requires upload/read-back and exact authority.

    A real SQLite writer transaction fences cooperating account/block/erasure
    changes through the fresh remote check and HEAD. The runtime lock separately
    prevents database replacement; it is not itself a writer fence.
    """
    validate_heartbeat_url(url)
    if not url:
        return False
    expected = result.get("authority")
    fields = {"enrolled", "lineage_id", "generation", "marker", "growth_generation"}
    if (result.get("offsite_verified") is not True or not isinstance(expected, dict)
            or set(expected) != fields or not expected.get("enrolled")
            or database.recovery_authority is None):
        raise BackupMonitorError("Backup monitoring requires a verified enrolled offsite snapshot; no success was signalled.")
    from .recovery import runtime_lock
    from .recovery_authority import local_state
    with runtime_lock(database.path), database.transaction() as connection:
        current = local_state(connection)
        if {name: current[name] for name in fields} != expected:
            raise BackupMonitorError("Backup changed before its monitoring check; take a fresh backup.")
        database.recovery_authority.require(expected)
        (sender or _send)(url)
    return True


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--send"]:
            raise ValueError
        _send_direct(sys.stdin.read(512))
    except Exception:
        raise SystemExit(2) from None
