"""Where the plugin keeps things, and the small helpers to read and write them.

    ~/.config/omarchy/amnezia/          700
        premium.json                    600  the key, this device's id, the picked location/protocol
        account.json                    600  the last account_info answer
        gateway-cache.json              600  fallback gateways that worked
        premium/<cc>-<proto>.json       600  configs Amnezia issued to this device
    $XDG_RUNTIME_DIR/omarchy-amnezia/   700
        active.json                          what this plugin brought up, and when
        <tunnel files>                  600  the config handed to the tunnel
        lock                                 one change at a time
    ~/.local/state/omarchy-amnezia/amnezia.log   names and errors only, never keys
"""

import contextlib
import fcntl
import json
import os
import time

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
BASE = os.environ.get("OMARCHY_AMNEZIA_DIR") or os.path.join(CONFIG_HOME, "omarchy", "amnezia")
PREMIUM = os.path.join(BASE, "premium.json")
ACCOUNT = os.path.join(BASE, "account.json")
GATEWAY_CACHE = os.path.join(BASE, "gateway-cache.json")
CONFIGS = os.path.join(BASE, "premium")

if os.environ.get("XDG_RUNTIME_DIR"):
    RUNTIME = os.path.join(os.environ["XDG_RUNTIME_DIR"], "omarchy-amnezia")
else:
    # Per uid and owner-only: a shared /tmp path would let another local user
    # plant the record of what is up.
    RUNTIME = "/tmp/omarchy-amnezia-%d" % os.getuid()
ACTIVE = os.path.join(RUNTIME, "active.json")
LOCK = os.path.join(RUNTIME, "lock")

STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
LOG = os.path.join(STATE_HOME, "omarchy-amnezia", "amnezia.log")
LOG_MAX = 512 * 1024


def ensure_dirs():
    for path in (BASE, CONFIGS, RUNTIME):
        os.makedirs(path, mode=0o700, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return default
    return value if value is not None else default


def write_json(path, value, mode=0o600):
    ensure_dirs()
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=1, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_private(path, text, mode=0o600):
    ensure_dirs()
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def remove(path):
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


class Busy(Exception):
    pass


@contextlib.contextmanager
def locked(wait=0.0):
    """One change at a time across the TUI, the bar and the CLI."""
    ensure_dirs()
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("another Amnezia action is still running")
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def is_locked():
    if not os.path.exists(LOCK):
        return False
    fd = os.open(LOCK, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def log(message):
    try:
        os.makedirs(os.path.dirname(LOG), mode=0o700, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(message).replace("\n", " ")[:400]))
    except OSError:
        pass
