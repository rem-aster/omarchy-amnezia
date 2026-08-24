# omarchy-amnezia

An [Omarchy Quattro](https://omarchy.org) shell plugin that puts Amnezia in the
bar instead of on the desktop: a shield icon, one switch for on/off, and a list
to pick which config the switch acts on. No Qt app, no tray icon, no background
service of its own.

```
┌─────────────────────────────────┐
│ 󰒃  Amnezia      AmneziaWG   ●━━ │
│    CONNECTED · 12m              │
│                                 │
│ Config                   berlin │
│ Endpoint    203.0.113.10:35001  │
│ Address            10.8.1.2/32  │
│ Transfer      ↓ 4.2 MB ↑ 812 KB │
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

## What it does not do

The plugin runs **AmneziaWG and plain WireGuard** — the two protocols that need
nothing but a userspace tool and a kernel interface. It does this by calling
`awg-quick`/`wg-quick`; it does not talk to `AmneziaVPN-service`, and it does not
replace the app.

Anything that needs a daemon of its own is out of scope, and the importer says
so plainly instead of half-importing it:

- OpenVPN, XRay/VLESS, Shadowsocks, IKEv2, SFTP/proxy containers
- Amnezia Free and Amnezia Premium subscription keys (they need the app's API
  client to fetch a server)
- split tunneling, kill switch, per-app routing, server setup over SSH

For those, keep [the app](https://github.com/amnezia-vpn/amnezia-client).

## Requirements

| For | Install |
| --- | --- |
| AmneziaWG configs | `amneziawg-tools` **and** `amneziawg-dkms` (AUR) |
| Plain WireGuard configs | `wireguard-tools` |
| DNS from a config's `DNS =` line | `openresolv` or `systemd-resolvconf` |
| Authorizing connect/disconnect | `polkit` (Omarchy's shell is the agent) |

`jq` and `python3` come with Omarchy. Run `omarchy-amnezia doctor` to see what
is missing on your box.

```bash
yay -S amneziawg-tools amneziawg-dkms
```

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

Configs land in `~/.config/omarchy/amnezia/configs/<name>.conf`, mode `600`. The
name becomes the network interface name, so it is limited to 15 characters of
`a-z A-Z 0-9 _ = + . -` — the same rule `awg-quick` enforces. You can also drop
a `.conf` in that directory by hand; the panel picks it up on its next refresh.

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

Connecting asks for authorization through polkit, which is Omarchy's own themed
dialog. One prompt per connect or disconnect.

Only one tunnel runs at a time: connecting a second config takes the first one
down.

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
omarchy-amnezia doctor               check the tools this plugin needs
```

## How it works

```
Panel.qml ── Service.qml ── bin/omarchy-amnezia ──┬── pkexec awg-quick up/down
  bar icon     poll + state       shell            │
  and panel    machine            plumbing         └── /sys/class/net (read-only state)
                                       │
                                       └── bin/amnezia-extract (vpn:// and JSON → .conf)
```

Reading state needs no privileges: a live tunnel is a directory under
`/sys/class/net`, and its byte counters are readable files there. Only bringing
a tunnel up or down goes through `pkexec`, one command at a time, so there is
nothing long-running to trust.

`bin/amnezia-extract` is the only part that had to follow the app's own format:
a `vpn://` key is base64url over a `qCompress`'d payload (a 4-byte big-endian
length in front of a zlib stream), which decodes to the Amnezia JSON config
whose `containers[].awg.last_config` holds the client `.conf` as a JSON string.
See `exportController.cpp` and `importController.cpp` in the client.

Files:

| Path | What |
| --- | --- |
| `~/.config/omarchy/amnezia/configs/<name>.conf` | your configs |
| `~/.config/omarchy/amnezia/state.json` | which config is selected |
| `$XDG_RUNTIME_DIR/omarchy-amnezia/<name>.since` | connect time, for the uptime line |

## Security notes

- A `.conf` holds your private key. The plugin keeps configs at mode `600` in a
  `700` directory and never copies them anywhere else.
- `awg-quick` runs `PreUp`/`PostUp` hooks **as root**. A config you did not
  write yourself can therefore run commands as root when you connect it. Read a
  config before importing it — `omarchy-amnezia edit <name>` opens it.
- Because of that, this plugin ships no passwordless polkit rule and no sudoers
  drop-in. If you decide you want one anyway, understand that it turns "anyone
  who can write a file in your home directory" into "root", and scope the rule
  as tightly as you can.
- There is no kill switch. If the tunnel drops, traffic goes out the default
  route as usual.

## Troubleshooting

**"Authorization cancelled"** — the polkit dialog was dismissed, or no polkit
agent is running. Omarchy's shell provides one; check `omarchy-amnezia doctor`.

**`resolvconf: command not found`** — the config has a `DNS =` line and nothing
on the box can apply it. Install `openresolv` or `systemd-resolvconf`, or delete
the `DNS` line with `omarchy-amnezia edit <name>`.

**`Unable to access interface: Protocol not supported`** — `awg-quick` is there
but the AmneziaWG kernel module is not. Install `amneziawg-dkms` and reboot, or
`amneziawg-go` for a userspace fallback.

**A config imports but will not connect** — run `./bin/omarchy-amnezia up <name>`
in a terminal; `awg-quick` prints exactly what it refused. Very old
`amneziawg-tools` releases do not understand the newer obfuscation keys
(`I1`–`I5`, `HeaderProtectionKey`, …) that recent Amnezia servers emit.

**The panel says nothing is there but a tunnel is up** — the plugin only knows
about interfaces named after a config in its own directory. A tunnel started
from `/etc/amnezia/amneziawg/` under a different name is invisible to it.

## Credits

- [Amnezia](https://github.com/amnezia-vpn/amnezia-client) and
  [amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) — the
  protocol, the config format, and the tool that does the actual work.
- [Omarchy](https://github.com/basecamp/omarchy) — the shell, and the
  first-party Tailscale and Dropbox widgets this one is patterned after.

Not affiliated with Amnezia or with Omarchy.

MIT licensed.
