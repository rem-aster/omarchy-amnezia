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
import signal
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


ACCOUNT_LOCK = os.path.join(RUNTIME, "account.lock")

# Past the CLI's own ceiling on an action (cli.ACTION_LIMIT, six minutes:
# a gateway call, a password prompt and the tunnel coming up), a holder has
# hung — typically it was mid-action when the machine went to sleep, and the
# wall clock counts the sleep — so it is stopped instead of refusing every
# action after it.
STALE_AFTER = 400


def _read_holder(fd):
    try:
        raw = os.pread(fd, 512, 0)
        holder = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return holder if isinstance(holder, dict) else {}


def _write_holder(fd, action):
    record = json.dumps({"pid": os.getpid(), "action": str(action)[:40], "started": time.time()})
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, record.encode("utf-8"), 0)
    except OSError:
        pass


def _ours(pid):
    """A live process of this user running this plugin's script — never stop
    anything else, whatever the lock file claims."""
    try:
        if os.stat("/proc/%d" % pid).st_uid != os.getuid():
            return False
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            return b"amnezia" in handle.read()
    except OSError:
        return False


def _stop(pid):
    for sig, grace in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        until = time.monotonic() + grace
        while time.monotonic() < until:
            if not os.path.exists("/proc/%d" % pid):
                return
            time.sleep(0.1)


def describe(holder):
    action = str(holder.get("action") or "action")
    started = holder.get("started")
    age = ""
    if isinstance(started, (int, float)):
        age = ", %ds" % max(0, int(time.time() - started))
    return "another Amnezia action is still running (%s%s) — try again in a moment" % (action, age)


@contextlib.contextmanager
def locked(wait=0.0, action="action", path=None):
    """One change at a time across the panel, the bar and the CLI. The holder
    leaves its pid, what it is doing and since when in the lock file, so a
    refusal can say what it is waiting for, and a holder that outlived
    STALE_AFTER is stopped rather than blocking everything after it."""
    ensure_dirs()
    fd = os.open(path or LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                holder = _read_holder(fd)
                pid = holder.get("pid")
                started = holder.get("started")
                if (isinstance(pid, int) and pid != os.getpid() and isinstance(started, (int, float))
                        and time.time() - started > STALE_AFTER and _ours(pid)):
                    log("stopping a stuck %s (pid %d, %ds old)" % (holder.get("action"), pid, time.time() - started))
                    _stop(pid)
                    continue
                if time.monotonic() >= deadline:
                    raise Busy(describe(holder))
                time.sleep(0.2)
        _write_holder(fd, action)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def is_locked():
    """Whether an action is running — a shared lock, so two status polls never
    get in each other's way. A holder past STALE_AFTER does not count: the next
    action will stop it."""
    if not os.path.exists(LOCK):
        return False
    fd = os.open(LOCK, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        started = _read_holder(fd).get("started")
        return not (isinstance(started, (int, float)) and time.time() - started > STALE_AFTER)
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
