"""Point the plugin's files at a throwaway directory before it is imported."""

import os
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = tempfile.mkdtemp(prefix="amnezia-test-")
os.environ["OMARCHY_AMNEZIA_DIR"] = os.path.join(ROOT, "config")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(ROOT, "run")
os.environ["XDG_STATE_HOME"] = os.path.join(ROOT, "state")
os.environ["AMNEZIA_DAEMON_SOCKET"] = os.path.join(ROOT, "no-daemon.socket")
os.environ["OMARCHY_AMNEZIA_NO_POKE"] = "1"
os.makedirs(os.environ["XDG_RUNTIME_DIR"], exist_ok=True)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)
