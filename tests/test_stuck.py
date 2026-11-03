"""Nothing may sit on the action lock for ever: a holder that hung (or slept
through a suspend) is stopped, slow network calls neither hold it nor run past
their deadline, and a password prompt nobody answers is given up on."""

import env  # noqa: F401  (must come first: it points the store at a temp dir)

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import fixtures
from amnezia_lib import app, cli, gateway, premium, store, tunnel

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")

# A stand-in for an action that hung: takes the lock the way the CLI does,
# then claims to have started long ago (as one that slept through a suspend
# would look) and never lets go. "amnezia" is in its command line, which is
# what the lock checks before stopping anything.
HOLDER = r"""
import os, sys, time, json
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from amnezia_lib import store
with store.locked(action="connect"):
    if sys.argv[2] == "old":
        fd = os.open(store.LOCK, os.O_RDWR)
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"pid": os.getpid(), "action": "connect", "started": time.time() - 3600}).encode(), 0)
        os.close(fd)
    print("holding", flush=True)
    time.sleep(120)
"""


def hold(age):
    process = subprocess.Popen([sys.executable, "-c", HOLDER, SCRIPTS, age, "amnezia-test"],
                               stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    assert process.stdout.readline().strip() == "holding"
    return process


class LockTest(unittest.TestCase):
    def tearDown(self):
        store.remove(store.LOCK)

    def test_fresh_holder_is_named_not_stopped(self):
        process = hold("new")
        try:
            with self.assertRaises(store.Busy) as caught:
                with store.locked(wait=0.5, action="disconnect"):
                    pass
            self.assertIn("(connect, ", str(caught.exception))
            self.assertIsNone(process.poll())
            self.assertTrue(store.is_locked())
        finally:
            process.kill()
            process.wait()
            process.stdout.close()

    def test_holder_from_before_a_suspend_is_stopped(self):
        process = hold("old")
        try:
            # It no longer counts as busy for the panel either.
            self.assertFalse(store.is_locked())
            started = time.monotonic()
            with store.locked(wait=0.5, action="disconnect"):
                fd = os.open(store.LOCK, os.O_RDONLY)
                holder = store._read_holder(fd)
                os.close(fd)
                self.assertEqual(holder["action"], "disconnect")
                self.assertEqual(holder["pid"], os.getpid())
            self.assertLess(time.monotonic() - started, 8)
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    def test_never_stops_a_stranger(self):
        # A stale record pointing at a process that is not this plugin's.
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            fd = os.open(store.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
            os.pwrite(fd, json.dumps({"pid": sleeper.pid, "action": "x", "started": 1}).encode(), 0)
            os.close(fd)
            self.assertFalse(store._ours(sleeper.pid))
            with store.locked(wait=0.1):
                pass
            self.assertIsNone(sleeper.poll())
        finally:
            sleeper.kill()
            sleeper.wait()


class SilentServer:
    """Accepts connections and never answers, like a gateway behind a
    network that is not quite back after a wake-up."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.held = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.held.append(conn)

    def close(self):
        self.sock.close()
        for conn in self.held:
            conn.close()


class DeadlineTest(unittest.TestCase):
    def setUp(self):
        self.saved = (gateway.MAIN_GATEWAY, gateway.PROXY_LIST_BASES, gateway.CALL_DEADLINE,
                      gateway.REQUEST_TIMEOUT, tunnel.ROOT_TIMEOUT, tunnel.ROOT_HELPER)
        self.server = SilentServer()
        gateway.MAIN_GATEWAY = "http://127.0.0.1:%d/" % self.server.port
        gateway.PROXY_LIST_BASES = ()
        shutil.rmtree(store.BASE, ignore_errors=True)
        self.p = premium.Premium()
        self.p.set_key(fixtures.premium_key())

    def tearDown(self):
        (gateway.MAIN_GATEWAY, gateway.PROXY_LIST_BASES, gateway.CALL_DEADLINE,
         gateway.REQUEST_TIMEOUT, tunnel.ROOT_TIMEOUT, tunnel.ROOT_HELPER) = self.saved
        self.server.close()

    def test_silent_gateway_gives_up_on_time(self):
        gateway.CALL_DEADLINE = 2
        started = time.monotonic()
        with self.assertRaises(premium.PremiumError):
            self.p.account(refresh=True)
        self.assertLess(time.monotonic() - started, 6)

    def test_refresh_does_not_hold_up_actions(self):
        gateway.CALL_DEADLINE = 3
        result = {}

        def refresh():
            try:
                app.refresh_account()
            except app.ActionError as error:
                result["error"] = str(error)

        worker = threading.Thread(target=refresh)
        worker.start()
        time.sleep(0.5)
        # While the refresh waits on the gateway, the action lock is free.
        self.assertFalse(store.is_locked())
        with store.locked(wait=0.1, action="disconnect"):
            pass
        # A second refresh at the same time answers from the cache at once.
        store.write_json(store.ACCOUNT, {"fetched_at": 1, "raw": fixtures.account_answer()})
        started = time.monotonic()
        self.assertEqual(app.refresh_account()["max_devices"], 5)
        self.assertLess(time.monotonic() - started, 1)
        worker.join(10)
        self.assertIn("gateway", result.get("error", ""))

    def test_unanswered_password_prompt_is_given_up(self):
        work = tempfile.mkdtemp()
        helper = os.path.join(work, "amnezia-root")
        with open(helper, "w") as handle:
            handle.write("#!/bin/bash\nsleep 30\n")
        os.chmod(helper, 0o755)
        tunnel.ROOT_HELPER = helper
        tunnel.ROOT_TIMEOUT = 1
        started = time.monotonic()
        with self.assertRaises(tunnel.TunnelError) as caught:
            tunnel.run_root(["down"])
        self.assertIn("not answered", str(caught.exception))
        self.assertLess(time.monotonic() - started, 5)
        shutil.rmtree(work, ignore_errors=True)


class CliCeilingTest(unittest.TestCase):
    def test_a_hung_command_exits_and_lets_go(self):
        saved = (cli.ACTION_LIMIT, cli._dispatch)

        def hang(argv):
            with store.locked(action="connect"):
                time.sleep(30)

        cli.ACTION_LIMIT = 1
        cli._dispatch = hang
        try:
            started = time.monotonic()
            self.assertEqual(cli.main(["up"]), 1)
            self.assertLess(time.monotonic() - started, 5)
            self.assertFalse(store.is_locked())
        finally:
            cli.ACTION_LIMIT, cli._dispatch = saved


if __name__ == "__main__":
    unittest.main()
