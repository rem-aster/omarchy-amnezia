#!/bin/bash

# Drives the real CLI, end to end, against a stand-in for AmneziaVPN.service.
#
# Nothing is stubbed inside the CLI here: it resolves the backend, talks to
# the socket through bin/amnezia-daemon, and reads its answers back. The
# service is fake, so the interesting assertion is the transcript — the exact
# activate payload the plugin sends, which has to match what the official
# client sends or the real service will refuse it.

set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLI="$HERE/../bin/omarchy-amnezia"
FAKE="$HERE/fake-amnezia-service"

# AF_UNIX paths are capped at ~108 bytes, so the socket cannot live under a
# long scratch path.
WORK="$(mktemp -d)"
SOCKET="$WORK/service.sock"
TRANSCRIPT="$WORK/transcript.jsonl"
SERVICE_PID=""

cleanup() {
  [[ -n $SERVICE_PID ]] && kill "$SERVICE_PID" 2>/dev/null
  rm -rf -- "$WORK"
}
trap cleanup EXIT

export OMARCHY_AMNEZIA_DIR="$WORK/config"
export XDG_RUNTIME_DIR="$WORK/run"
export AMNEZIA_DAEMON_SOCKET="$SOCKET"
mkdir -p "$XDG_RUNTIME_DIR"

FAILURES=0

ok() { printf '  ✓ %s\n' "$1"; }
fail() {
  printf '  ✗ %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

expect() {
  local label="$1" want="$2" got="$3"
  if [[ $want == "$got" ]]; then ok "$label"; else fail "$label (want '$want', got '$got')"; fi
}

amnezia() { "$CLI" "$@"; }
status_field() { amnezia status --json | jq -r "$1"; }
activate_field() { jq -r "select(.type == \"activate\") | $1" "$TRANSCRIPT" | tail -n 1; }
request_count() { jq -s --arg type "$1" '[.[] | select(.type == $type)] | length' "$TRANSCRIPT"; }

echo "omarchy-amnezia daemon backend"

# --- with no service running ------------------------------------------------

expect "falls back to awg-quick with no service socket" "quick" "$(status_field '.tools.backend')"

# --- bring the stand-in up -------------------------------------------------

python3 "$FAKE" "$SOCKET" "$TRANSCRIPT" >"$WORK/ready" 2>"$WORK/service.err" &
SERVICE_PID=$!
for _ in $(seq 200); do
  [[ -S $SOCKET ]] && break
  sleep 0.02 2>/dev/null || true
done
if [[ ! -S $SOCKET ]]; then
  echo "the stand-in service did not start:"
  cat "$WORK/service.err"
  exit 1
fi

expect "picks the service up automatically once its socket is there" "daemon" \
  "$(status_field '.tools.backend')"
expect "reports the service as available" "true" "$(status_field '.tools.daemon')"

# --- a config to run ------------------------------------------------------

cat >"$WORK/berlin.conf" <<'CONF'
[Interface]
Address = 10.8.1.2/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MTI=
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 30
H1 = 1234567890
I1 = <b 0xdeadbeef>

[Peer]
PublicKey = c2VydmVycHVibGljc2VydmVycHVibGljc2VydmVyMQ=
PresharedKey = cHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrMTI=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 127.0.0.1:35001
PersistentKeepalive = 25
CONF

cat >"$WORK/oslo.conf" <<'CONF'
[Interface]
Address = 10.9.0.5/32
PrivateKey = b3Nsb3ByaXZhdGVrZXlvc2xvcHJpdmF0ZWtleW9zMTI=
[Peer]
PublicKey = b3Nsb3B1YmxpY2tleW9zbG9wdWJsaWNrZXlvc2xvMTI=
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:51820
CONF

amnezia import "$WORK/berlin.conf" --name berlin >/dev/null
amnezia import "$WORK/oslo.conf" --name oslo >/dev/null
expect "the service backend needs no awg-quick to import" "2" \
  "$(amnezia list --json | jq 'length')"

# --- connect --------------------------------------------------------------

amnezia up berlin >/dev/null || fail "up berlin failed"
expect "the service reports the tunnel up" "true" "$(status_field '.active')"
expect "the panel learns which profile is running" "berlin" "$(status_field '.activeProfile')"
expect "counters come from the service, not sysfs" "4096" "$(status_field '.rxBytes')"
expect "the live profile is flagged in the list" "berlin" \
  "$(amnezia list --json | jq -r '.[] | select(.active) | .name')"

# The payload is the part the real service validates, field by field
# (Daemon::parseConfig). These mirror LocalSocketController::activate().
expect "sends the client private key" "cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MTI=" \
  "$(activate_field '.privateKey')"
expect "sends the server public key" "c2VydmVycHVibGljc2VydmVycHVibGljc2VydmVyMQ=" \
  "$(activate_field '.serverPublicKey')"
expect "sends the preshared key" "cHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrcHNrMTI=" \
  "$(activate_field '.serverPskKey')"
expect "sends the port as a number" "35001" "$(activate_field '.serverPort')"
expect "sends the client address" "10.8.1.2/32" "$(activate_field '.deviceIpv4Address')"
expect "sends the resolved endpoint as the server address" "127.0.0.1" \
  "$(activate_field '.serverIpv4AddrIn')"
expect "sends the same address as the gateway" "127.0.0.1" \
  "$(activate_field '.serverIpv4Gateway')"
expect "keeps the endpoint off the tunnel" "127.0.0.1" \
  "$(activate_field '.excludedAddresses[0]')"
expect "sends the ULA the client hardcodes" "fd58:baa6:dead::1" \
  "$(activate_field '.deviceIpv6Address')"
expect "sends the MTU as a string, not a number" "string" \
  "$(activate_field '.deviceMTU | type')"
expect "splits the DNS line" "1.1.1.1" "$(activate_field '.primaryDnsServer')"
expect "sends the second resolver too" "1.0.0.1" "$(activate_field '.secondaryDnsServer')"
expect "sends keepalive as a string" "25" "$(activate_field '.persistentKeepalive')"
expect "forwards the obfuscation knobs verbatim" "4 40 70 30 1234567890 <b 0xdeadbeef>" \
  "$(activate_field '[.Jc, .Jmin, .Jmax, .S1, .H1, .I1] | join(" ")')"
expect "marks the IPv6 default route as IPv6" "true" \
  "$(activate_field '.allowedIPAddressRanges[] | select(.address == "::") | .isIpv6 | tostring')"
expect "marks the IPv4 default route as IPv4" "false" \
  "$(activate_field '.allowedIPAddressRanges[] | select(.address == "0.0.0.0") | .isIpv6 | tostring')"

# --- switch ---------------------------------------------------------------

amnezia up oslo >/dev/null || fail "up oslo failed"
expect "switching hands the service the new config" "10.9.0.5/32" \
  "$(activate_field '.deviceIpv4Address')"
expect "switching does not stop the service first" "0" "$(request_count deactivate)"
expect "the panel follows the switch" "oslo" "$(status_field '.activeProfile')"
expect "still only one profile is live" "1" \
  "$(amnezia list --json | jq '[.[] | select(.active)] | length')"

# --- a tunnel raised outside the plugin -----------------------------------

# A record pointing at a profile that no longer exists must not win over the
# address the service actually reports.
printf 'ghost\n' >"$XDG_RUNTIME_DIR/omarchy-amnezia/active"
expect "a stale record does not mislabel the live tunnel" "oslo" "$(status_field '.activeProfile')"

# Take the config off the disk while the service keeps running it — that is
# what the GUI connecting to a server we never imported looks like from here.
rm -f "$OMARCHY_AMNEZIA_DIR/configs/oslo.conf"
expect "a tunnel from a config we do not have still reads as up" "true" \
  "$(status_field '.active')"
expect "and is marked as not one of ours" "true" "$(status_field '.activeForeign')"
expect "with no profile name attached" "" "$(status_field '.activeProfile')"

# --- disconnect -----------------------------------------------------------

amnezia down >/dev/null || fail "down failed"
expect "down stops the service's tunnel" "false" "$(status_field '.active')"
expect "down reached the service" "1" "$(request_count deactivate)"

OUT="$(amnezia down 2>&1)"
expect "a second down is quiet" "1" "$(grep -c 'nothing is connected' <<<"$OUT")"

# --- a service that refuses ------------------------------------------------

kill "$SERVICE_PID" 2>/dev/null
wait "$SERVICE_PID" 2>/dev/null
rm -f "$SOCKET"
python3 "$FAKE" "$SOCKET" "$WORK/refused.jsonl" --refuse >/dev/null 2>&1 &
SERVICE_PID=$!
for _ in $(seq 200); do
  [[ -S $SOCKET ]] && break
  sleep 0.02 2>/dev/null || true
done

OUT="$(amnezia up berlin 2>&1)"
expect "a refusal is reported, not swallowed" "1" \
  "$(grep -c 'rejected the config' <<<"$OUT")"
expect "and nothing is left claiming to be up" "false" "$(status_field '.active')"

# --- forcing the other backend --------------------------------------------

expect "the backend can be forced back to awg-quick" "quick" \
  "$(OMARCHY_AMNEZIA_BACKEND=quick "$CLI" status --json | jq -r '.tools.backend')"

echo
if ((FAILURES == 0)); then
  echo "all checks passed"
else
  echo "$FAILURES check(s) failed"
fi
exit $((FAILURES > 0))
