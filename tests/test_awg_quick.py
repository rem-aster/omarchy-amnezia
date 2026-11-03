"""AmneziaWG without the app: awg-quick's real reason reaches the person,
and a machine that cannot make the interface is told so before any password
prompt."""

import env  # noqa: F401  (must come first: it points the store at a temp dir)

import os
import subprocess
import unittest

import fixtures
from amnezia_lib import crypto, premium, tunnel

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "amnezia-root")

# What awg-quick prints in each case, from its linux.bash: the steps, the
# failure, then its own teardown of an interface that may never have existed.
NO_MODULE = """[#] ip link add amnezia type amneziawg
Error: Unknown device type.
[#] ip link delete dev amnezia
Cannot find device "amnezia"
"""
OLD_TOOLS = """[#] ip link add amnezia type amneziawg
[#] awg setconf amnezia /dev/fd/63
Line unrecognized: `HeaderProtectionKey=7f1b0c9a2e'
Configuration parsing error
[#] ip link delete dev amnezia
"""
USERSPACE_FAILED = """[#] ip link add amnezia type amneziawg
Error: Unknown device type.
[!] Missing WireGuard (Amnezia VPN) kernel module. Falling back to slow userspace implementation.
[#] amneziawg-go amnezia
ERROR: (amnezia) Failed to create TUN device: operation not permitted
[#] ip link delete dev amnezia
Cannot find device "amnezia"
"""


def helper_says(output):
    """awg_hint(awg_reason(output)), run from the helper's own functions."""
    with open(HELPER) as handle:
        source = handle.read()
    functions = "".join(source[source.index(name):source.index("\n}\n", source.index(name)) + 3]
                        for name in ("awg_reason() {", "awg_hint() {"))
    script = functions + 'awg_hint "$(awg_reason "$1")"\n'
    return subprocess.run(["bash", "-c", script, "bash", output], capture_output=True, text=True,
                          check=True).stdout.strip()


class ReasonTest(unittest.TestCase):
    def test_missing_module_is_not_cannot_find_device(self):
        said = helper_says(NO_MODULE)
        self.assertNotIn("Cannot find device", said)
        self.assertIn("amneziawg module is not built for kernel", said)

    def test_old_tools_name_the_option(self):
        said = helper_says(OLD_TOOLS)
        self.assertIn("HeaderProtectionKey", said)
        self.assertIn("update amneziawg-tools", said)

    def test_userspace_failure_wins_over_the_kernel_attempt(self):
        said = helper_says(USERSPACE_FAILED)
        self.assertIn("Failed to create TUN device", said)
        self.assertNotIn("Unknown device type", said)


class PreflightTest(unittest.TestCase):
    def test_no_password_prompt_when_nothing_can_make_the_interface(self):
        p = premium.Premium()
        p.set_key(fixtures.premium_key())
        private, public = crypto.wireguard_keypair()
        record = p._record_from_answer(fixtures.config_answer("awg"), "de", "awg", private, public)
        saved = (tunnel.have, tunnel.awg_kernel, tunnel.run_root, tunnel.daemon_socket)
        calls = []
        tunnel.have = lambda command: command in ("awg-quick", "pkexec")
        tunnel.awg_kernel = lambda: ""
        tunnel.run_root = lambda args: calls.append(args)
        tunnel.daemon_socket = lambda: ""
        try:
            with self.assertRaises(tunnel.TunnelError) as caught:
                tunnel.up(record)
            self.assertIn("amneziawg-go", str(caught.exception))
            self.assertEqual(calls, [])
            # With amneziawg-go to stand in, it goes ahead.
            tunnel.have = lambda command: command in ("awg-quick", "pkexec", "amneziawg-go")
            tunnel.up(record)
            self.assertEqual(calls[-1][0], "up-awg")
        finally:
            tunnel.have, tunnel.awg_kernel, tunnel.run_root, tunnel.daemon_socket = saved


if __name__ == "__main__":
    unittest.main()
