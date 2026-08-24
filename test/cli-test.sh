#!/bin/bash

# Exercises bin/omarchy-amnezia without a kernel, a tunnel, or root.
#
# The CLI only runs `main` when it is executed, so this sources it for its
# functions and replaces the two things that need a real machine: `run_priv`
# (which would call awg-quick) and `iface_up` (which reads /sys/class/net).
# The stub keeps a fake "live interface" in $FAKE_UP so up/down/toggle and
# active-profile detection are all testable.

set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLI="$HERE/../bin/omarchy-amnezia"
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

export OMARCHY_AMNEZIA_DIR="$WORK/config"
export XDG_RUNTIME_DIR="$WORK/run"
mkdir -p "$XDG_RUNTIME_DIR"

FAILURES=0
FAKE_UP=""
PRIV_LOG="$WORK/priv.log"
: >"$PRIV_LOG"

# shellcheck source=../bin/omarchy-amnezia
source "$CLI"

iface_up() {
  [[ -n $FAKE_UP && $1 == "$FAKE_UP" ]]
}

iface_bytes() {
  printf '%s\n' "$([[ $2 == rx ]] && printf 4096 || printf 2048)"
}

run_priv() {
  printf '%s\n' "$*" >>"$PRIV_LOG"
  local action="${2:-}" target="${3:-}"
  target="${target##*/}"
  target="${target%.conf}"
  case "$action" in
    up) FAKE_UP="$target" ;;
    down) FAKE_UP="" ;;
  esac
}

# The stub stands in for both tools, so every config looks runnable.
quick_tool() {
  printf 'awg-quick\n'
}

ok() {
  printf '  ✓ %s\n' "$1"
}

fail() {
  printf '  ✗ %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

expect() {
  local label="$1" want="$2" got="$3"
  if [[ $want == "$got" ]]; then
    ok "$label"
  else
    fail "$label (want '$want', got '$got')"
  fi
}

status_field() {
  cmd_status --json | jq -r "$1"
}

write_conf() {
  local path="$1" endpoint="$2"
  shift 2
  {
    echo "[Interface]"
    echo "Address = 10.8.1.2/32"
    echo "DNS = 1.1.1.1"
    echo "PrivateKey = $(printf 'k%.0s' {1..42})="
    printf '%s\n' "$@"
    echo
    echo "[Peer]"
    echo "PublicKey = $(printf 'p%.0s' {1..42})="
    echo "AllowedIPs = 0.0.0.0/0, ::/0"
    echo "Endpoint = $endpoint"
  } >"$path"
}

echo "omarchy-amnezia CLI"

# --- import ----------------------------------------------------------------

write_conf "$WORK/awg.conf" "203.0.113.10:35001" "Jc = 4" "H1 = 1234567890"
write_conf "$WORK/plain.conf" "203.0.113.20:51820"

cmd_import "$WORK/awg.conf" --name berlin >/dev/null 2>&1
expect "imports an AmneziaWG config" "awg" "$(protocol_of "$(profile_path berlin)")"

cmd_import "$WORK/plain.conf" --name oslo >/dev/null 2>&1
expect "imports a plain WireGuard config" "wireguard" "$(protocol_of "$(profile_path oslo)")"

expect "the imported config is not world readable" "600" \
  "$(stat -c '%a' "$(profile_path berlin)")"

cmd_import "$WORK/awg.conf" --name berlin >/dev/null 2>&1
expect "a second import of the same name lands beside it" "berlin-2" \
  "$(profile_names | grep -c . >/dev/null && profile_names | grep '^berlin-2$')"

expect "the first import becomes the selection" "berlin" "$(read_state selected)"

# vpn:// keys are base64url over a Qt-compressed Amnezia JSON config.
KEY="$(python3 - "$WORK/awg.conf" <<'PY'
import base64, json, struct, sys, zlib
conf = open(sys.argv[1]).read()
config = {
    "containers": [{"container": "amnezia-awg",
                    "awg": {"last_config": json.dumps({"config": conf})}}],
    "defaultContainer": "amnezia-awg",
    "description": "Vienna Relay",
}
raw = json.dumps(config).encode()
blob = struct.pack(">I", len(raw)) + zlib.compress(raw, 8)
print("vpn://" + base64.urlsafe_b64encode(blob).decode().rstrip("="))
PY
)"
cmd_import "$KEY" >/dev/null 2>&1
expect "imports a vpn:// key and names it from the config" "awg" \
  "$(protocol_of "$(profile_path vienna-relay)")"

OUT="$(cmd_import "$WORK/nope.conf" 2>&1)"
expect "refuses a file that is not there" "1" "$(grep -c 'no such file' <<<"$OUT")"

printf 'client\ndev tun\nremote 198.51.100.1\n' >"$WORK/legacy.ovpn"
OUT="$(cmd_import "$WORK/legacy.ovpn" 2>&1)"
expect "refuses OpenVPN with a reason" "1" "$(grep -c 'OpenVPN' <<<"$OUT")"

# --- select ----------------------------------------------------------------

cmd_select oslo
expect "select records the choice" "oslo" "$(status_field '.selected')"
expect "select alone connects nothing" "false" "$(status_field '.active')"

OUT="$(cmd_select nothere 2>&1)"
expect "select refuses an unknown profile" "1" "$(grep -c 'no such profile' <<<"$OUT")"

# --- up / down / toggle ----------------------------------------------------

cmd_up berlin >/dev/null
expect "up brings the named profile up" "berlin" "$(status_field '.activeProfile')"
expect "up reports the tunnel as active" "true" "$(status_field '.active')"
expect "up moves the selection to what it connected" "berlin" "$(status_field '.selected')"
expect "the panel sees transfer counters" "4096" "$(status_field '.rxBytes')"
expect "the live profile is flagged in the list" "berlin" \
  "$(cmd_list --json | jq -r '.[] | select(.active) | .name')"

cmd_up oslo >/dev/null
expect "up on another profile switches to it" "oslo" "$(status_field '.activeProfile')"
expect "only one tunnel is up at a time" "1" "$(cmd_list --json | jq '[.[] | select(.active)] | length')"
expect "switching took the old tunnel down first" "1" \
  "$(grep -c 'down .*berlin.conf' "$PRIV_LOG")"

cmd_toggle >/dev/null
expect "toggle takes the live tunnel down" "false" "$(status_field '.active')"

cmd_toggle >/dev/null
expect "toggle brings the selection back up" "oslo" "$(status_field '.activeProfile')"

cmd_down >/dev/null
expect "down with no argument disconnects whatever is up" "false" "$(status_field '.active')"

OUT="$(cmd_down 2>&1)"
expect "down is quiet when nothing is connected" "1" "$(grep -c 'nothing is connected' <<<"$OUT")"

# --- removal -------------------------------------------------------------

cmd_up berlin >/dev/null
cmd_remove berlin >/dev/null
expect "removing the live profile disconnects it" "false" "$(status_field '.active')"
expect "removing a profile drops it from the list" "0" \
  "$(cmd_list --json | jq '[.[] | select(.name == "berlin")] | length')"
expect "the selection moves off the removed profile" "1" \
  "$(status_field '.selected' | grep -cv '^berlin$')"

# --- names ---------------------------------------------------------------

expect "rejects a name awg-quick could not use as an interface" "1" \
  "$(valid_name "this-name-is-far-too-long" && echo 0 || echo 1)"
expect "rejects a name that walks out of the config dir" "1" \
  "$(valid_name "../etc/passwd" && echo 0 || echo 1)"
expect "accepts an ordinary name" "0" \
  "$(valid_name "berlin.2" && echo 0 || echo 1)"

echo
if ((FAILURES == 0)); then
  echo "all checks passed"
else
  echo "$FAILURES check(s) failed"
fi
exit $((FAILURES > 0))
