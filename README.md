# omarchy-amnezia

An [Omarchy Quattro](https://omarchy.org) shell plugin that puts Amnezia in the
bar instead of on the desktop: a shield icon, one switch for on/off, and a list
to pick which config the switch acts on. No Qt app, no tray icon.

It works two ways and picks for itself: if the official client's service is
already installed, the plugin drives that — no password prompts at all. If it
is not, the plugin raises the tunnel itself with `awg-quick` through polkit.
See [Backends](#backends).

```
┌─────────────────────────────────┐
│ 󰒃  Amnezia      AmneziaWG   ●━━ │
│    CONNECTED · 12m              │
│                                 │
│ Config                   berlin │
│ Endpoint    203.0.113.10:35001  │
│ Address            10.8.1.2/32  │
│ Transfer      ↓ 4.2 MB ↑ 812 KB │
│ Via         AmneziaVPN service  │
│ ─────────────────────────────── │
│ CONFIGS                         │
│ 󰒃 berlin                   󰄬 󰏤 │
│   AmneziaWG · 203.0.113.10:35001│
│ 󰒃 amsterdam                  󰐊 │
│   AmneziaWG · 198.51.100.7:35001│
└─────────────────────────────────┘
```

## What it does

- Shows whether a tunnel is up, in the bar.
- Turns it on and off — the switch in the panel, right click on the bar icon,
  or `t` in the panel.
- Lists your configs and lets you pick the one the switch acts on. Picking
  while connected moves the tunnel over right away (configurable).
- Imports configs from the Amnezia app: a `.conf` file, an exported
  `.vpn`/`.json` config, or a `vpn://` key.
- Uses the official client's service when it is installed, so switching costs
  no password and the transfer counters come off the real handshake.

## What it does not do

The plugin runs **AmneziaWG and plain WireGuard** — the two protocols that need
nothing more than a tunnel interface. Everything else about the app stays in the
app: this is a switch and a config list, not a client.

Anything that needs a daemon of its own is out of scope, and the importer says
so plainly instead of half-importing it:

- OpenVPN, XRay/VLESS, Shadowsocks, IKEv2, SFTP/proxy containers
- a raw Amnezia Free/Premium `vpn://` key — see [Subscription
  keys](#subscription-keys) for what to import instead
- split tunneling, kill switch, per-app routing, server setup over SSH

For those, keep [the app](https://github.com/amnezia-vpn/amnezia-client).

## Requirements

`jq` and `python3` come with Omarchy, and that is all the **service backend**
needs — the AmneziaVPN service ships its own tunnel binary.

The **awg-quick backend** needs the tunnel tools on the box:

| For | Install |
| --- | --- |
| AmneziaWG configs | `amneziawg-tools` **and** `amneziawg-dkms` (AUR) |
| Plain WireGuard configs | `wireguard-tools` |
| DNS from a config's `DNS =` line | `openresolv` or `systemd-resolvconf` |
| Authorizing connect/disconnect | `polkit` (Omarchy's shell is the agent) |

```bash
yay -S amneziawg-tools amneziawg-dkms
```

Run `omarchy-amnezia doctor` to see which backend is in force and what, if
anything, is missing for it.

## Install

```bash
omarchy plugin add https://github.com/rem-aster/omarchy-amnezia.git --enable --yes
omarchy bar move io.github.rem-aster.amnezia   # optional, it lands on the right
```

Plugins run unsandboxed inside `omarchy-shell`, so read the code first — that is
why `omarchy plugin add` leaves a plugin disabled unless you pass `--enable`.

To take it off the bar again:

```bash
omarchy plugin disable io.github.rem-aster.amnezia
omarchy plugin remove io.github.rem-aster.amnezia
```

## Import a config

In the Amnezia app, use *Sharing* to export a connection, then hand the file or
the key to the CLI. The plugin ships it at `bin/omarchy-amnezia` inside the
plugin directory; add that to your `PATH` or call it by path.

```bash
cd ~/.config/omarchy/plugins/io.github.rem-aster.amnezia

# a WireGuard/AmneziaWG .conf
./bin/omarchy-amnezia import ~/Downloads/amnezia_for_awg.conf --name berlin

# an exported Amnezia config, or a vpn:// key
./bin/omarchy-amnezia import ~/Downloads/berlin.vpn
./bin/omarchy-amnezia import "vpn://AAAA…"

# or from the clipboard
wl-paste | ./bin/omarchy-amnezia import - --name berlin
```

### Subscription keys

A `vpn://` key from Amnezia Free or Premium will not import, and the message
says so. Those keys carry the config with the private key left as a
`$WIREGUARD_CLIENT_PRIVATE_KEY` placeholder: the app generates the key pair
itself and registers the public half with Amnezia's API, so nobody else can
complete the key — the server would not recognise a key pair it never saw
(`subscriptionController.cpp`).

The app can hand you a finished config for exactly this purpose. Open the
server in the app, then **Configuration Files** ("for router setup or the
AmneziaWG app"), issue one for a country, and import the `.conf` it saves:

```bash
./bin/omarchy-amnezia import ~/Documents/de.conf --name germany
```

That export asks the API for a fresh key pair, so it counts as its own
registered config — the app warns that issuing a new one stops the previous
one working, and has a *Revoke* for it on the same screen.

Configs land in `~/.config/omarchy/amnezia/configs/<name>.conf`, mode `600`. The
name becomes the network interface name, so it is limited to 15 characters of
`a-z A-Z 0-9 _ = + . -` — the same rule `awg-quick` enforces. You can also drop
a `.conf` in that directory by hand; the panel picks it up on its next refresh.

## Backends

There are two ways to raise a tunnel, and `auto` (the default) picks whichever
the machine can do:

| | **service** | **awg-quick** |
| --- | --- | --- |
| Needs | the official client installed | `amneziawg-tools` / `wireguard-tools` |
| Password | never | one polkit prompt per switch |
| Runs as root | the client's service, always | nothing between switches |
| Interface | `amn0` (the service's own) | named after the config |
| Transfer counters | from the real handshake | from `/sys/class/net` |

```bash
omarchy-amnezia backend                # what is in force, and why
omarchy-amnezia backend daemon         # always use the service
omarchy-amnezia backend quick          # always use awg-quick
omarchy-amnezia backend auto           # decide per run (default)
```

`OMARCHY_AMNEZIA_BACKEND=quick` overrides the stored setting for one command.

### The service backend

The official client's installer leaves `AmneziaVPN.service` running as root from
boot, and its GUI is just a client of it: newline-delimited JSON over
`/var/run/amneziavpn/daemon.socket`, commands `activate` / `deactivate` /
`status`. The socket is opened world-accessible and the service does not check
who is calling — which is exactly why that client never asks for a password
after install. This plugin speaks the same protocol, so on a machine that has
the client it gets the same no-prompt switching, plus a `status` answer carrying
the handshake's own byte counters.

What that means in practice:

- **The client has to be installed.** The service raises the tunnel by running
  the `amneziawg-go` binary sitting next to it in `/opt/AmneziaVPN/bin`, so the
  service alone is not enough — and no kernel module is needed either.
- **The app and the plugin share one tunnel.** The service runs a single
  interface, `amn0`. Connect from the app and the panel shows it; the panel
  labels it *not imported here* when it is a config you never imported, and the
  switch can still turn it off.
- **The protocol is private.** It is inherited from mozilla-vpn and validated
  strictly: an upstream change to the field list would show up here as a switch
  that stops working. `omarchy-amnezia backend quick` is the way out.

### The awg-quick backend

No client, nothing running as root between switches: each connect is one
`pkexec awg-quick up <config>`, so Omarchy's own polkit dialog authorizes it.
Reading state needs no privileges at all — a live tunnel is a virtual interface
named after its config under `/sys/class/net`, byte counters included.

## Use it

| Where | Action |
| --- | --- |
| Bar icon, left click | open the panel |
| Bar icon, right click | connect / disconnect |
| Bar icon, middle click | refresh |
| Panel switch | connect / disconnect |
| Config row, click | make it the selected config |
| Config row, ▶ / ⏸ button | connect / disconnect that config |
| `j` `k` / arrows | move the cursor |
| `enter`, `space` | activate the row under the cursor |
| `c` | connect the config under the cursor |
| `t` | connect / disconnect |
| `d` | disconnect |
| `r` | refresh |
| `esc` | close |

On the awg-quick backend, connecting asks for authorization through polkit —
Omarchy's own themed dialog, one prompt per connect or disconnect. On the
service backend there is no prompt at all.

Only one tunnel runs at a time: connecting a second config takes the first one
down (the service does that switch itself).

### From a script or a keybind

The widget registers an IPC target, so a Hyprland bind can toggle the tunnel
without opening anything:

```bash
omarchy-shell amnezia vpnToggle          # connect / disconnect
omarchy-shell amnezia vpnUp berlin       # connect a named config
omarchy-shell amnezia vpnDown
omarchy-shell amnezia pick amsterdam     # change the selection
omarchy-shell amnezia status
omarchy-shell shell toggle io.github.rem-aster.amnezia   # open the panel
```

Or drive the CLI directly — `omarchy-amnezia status --json` is the same data the
panel reads.

## Settings

Both live inline on the widget's entry in `~/.config/omarchy/shell.json`:

```json
{ "id": "io.github.rem-aster.amnezia", "refreshIntervalSec": 15, "switchWhenConnected": "On" }
```

- **`refreshIntervalSec`** (default `15`) — how often the panel re-reads the
  tunnel state and the transfer counters.
- **`switchWhenConnected`** (default `On`) — with `On`, picking another config
  while connected moves the tunnel to it immediately. With `Off`, picking only
  decides what the switch will connect next.

## CLI

```
omarchy-amnezia status [--json]      what is connected, plus every config
omarchy-amnezia list [--json]        list configs
omarchy-amnezia up [name]            connect (defaults to the selected config)
omarchy-amnezia down [name]          disconnect
omarchy-amnezia toggle [name]        connect if off, disconnect if on
omarchy-amnezia select <name>        choose what the switch acts on
omarchy-amnezia import <src>         import a .conf, a .vpn/.json, or a vpn:// key
omarchy-amnezia remove <name>        delete a config
omarchy-amnezia rename <old> <new>   rename a config
omarchy-amnezia edit [name]          open a config in $EDITOR
omarchy-amnezia backend [name]       show or set how tunnels are raised
omarchy-amnezia doctor               check the backend and what it needs
```

## How it works

```
Panel.qml ── Service.qml ── bin/omarchy-amnezia ──┬── bin/amnezia-daemon
  bar icon     poll + state       shell           │     └── AmneziaVPN.service socket
  and panel    machine            plumbing        │
                                                  ├── pkexec awg-quick up/down
                                                  ├── /sys/class/net (state, no privileges)
                                                  └── bin/amnezia-extract
                                                        (vpn:// and JSON → .conf)
```

Both backends keep the privileged part to a single call with nothing of ours
long-running: one `activate` message to a service that was already there, or one
`pkexec awg-quick`. Neither needs a helper installed, a sudoers entry, or a
polkit rule of its own.

`bin/amnezia-extract` is the only part that had to follow the app's own format:
a `vpn://` key is base64url over a `qCompress`'d payload (a 4-byte big-endian
length in front of a zlib stream), which decodes to the Amnezia JSON config
whose `containers[].awg.last_config` holds the client `.conf` as a JSON string.
See `exportController.cpp` and `importController.cpp` in the client.

Files:

| Path | What |
| --- | --- |
| `~/.config/omarchy/amnezia/configs/<name>.conf` | your configs |
| `~/.config/omarchy/amnezia/state.json` | the selected config, and the backend preference |
| `$XDG_RUNTIME_DIR/omarchy-amnezia/<name>.since` | connect time, for the uptime line |
| `$XDG_RUNTIME_DIR/omarchy-amnezia/active` | which config the service backend raised |

## Security notes

- A `.conf` holds your private key. The plugin keeps configs at mode `600` in a
  `700` directory and never copies them anywhere else.
- **On the service backend, the trust decision was made at install time, not
  here.** `AmneziaVPN.service` runs as root from boot with a socket any local
  process can command, and it never checks who is calling. That is what makes it
  prompt-free — and it means any process on the machine can already point your
  traffic at a server of its choosing, whether or not this plugin is installed.
  The plugin uses that property; it does not create it. If you would rather not
  have it, uninstall the client and run `omarchy-amnezia backend quick`.
- **On the awg-quick backend, `PreUp`/`PostUp` hooks run as root.** A config you
  did not write yourself can therefore run commands as root when you connect it.
  Read a config before importing it — `omarchy-amnezia edit <name>` opens it.
- Because of that hook behaviour, this plugin ships no passwordless polkit rule
  and no sudoers drop-in for `awg-quick`. If you decide you want one anyway,
  understand that it turns "anyone who can write a file in your home directory"
  into "root", and scope the rule as tightly as you can.
- There is no kill switch on either backend. If the tunnel drops, traffic goes
  out the default route as usual.

## Troubleshooting

**"Authorization cancelled"** — the polkit dialog was dismissed, or no polkit
agent is running. Omarchy's shell provides one; check `omarchy-amnezia doctor`.
Only the awg-quick backend prompts at all.

**"the AmneziaVPN service is not running"** — the socket went away, usually
because the client was uninstalled or `systemctl stop AmneziaVPN` was run.
`omarchy-amnezia backend auto` will fall back to awg-quick on the next command;
`systemctl start AmneziaVPN` brings the other path back.

**"the AmneziaVPN service rejected the config"** — the service validates every
field of the config it is handed and says nothing about which one it disliked.
Its own log does: `/var/log/AmneziaVPN`. A config whose `Endpoint` is a hostname
that no longer resolves is the common case.

**`resolvconf: command not found`** — the config has a `DNS =` line and nothing
on the box can apply it. Install `openresolv` or `systemd-resolvconf`, or delete
the `DNS` line with `omarchy-amnezia edit <name>`.

**`Unable to access interface: Protocol not supported`** — `awg-quick` is there
but the AmneziaWG kernel module is not. Install `amneziawg-dkms` and reboot, or
`amneziawg-go` for a userspace fallback.

**"This is an Amnezia subscription key"** — see [Subscription
keys](#subscription-keys): export a config file from the app instead of pasting
the `vpn://` key.

**A config imports but will not connect** — run `./bin/omarchy-amnezia up <name>`
in a terminal; `awg-quick` prints exactly what it refused. Very old
`amneziawg-tools` releases do not understand the newer obfuscation keys
(`I1`–`I5`, `HeaderProtectionKey`, …) that recent Amnezia servers emit.

**The panel says nothing is there but a tunnel is up** — on the awg-quick
backend the plugin only knows about interfaces named after a config in its own
directory, so a tunnel raised from `/etc/amnezia/amneziawg/` under another name
is invisible to it. On the service backend it will see the tunnel but show the
config as *not imported here*, because the service reports an address that
matches none of your configs.

## Credits

- [Amnezia](https://github.com/amnezia-vpn/amnezia-client) and
  [amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) — the
  protocol, the config format, and the tool that does the actual work.
- [Omarchy](https://github.com/basecamp/omarchy) — the shell, and the
  first-party Tailscale and Dropbox widgets this one is patterned after.

Not affiliated with Amnezia or with Omarchy.

MIT licensed.
