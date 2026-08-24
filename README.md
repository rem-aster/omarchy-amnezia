# omarchy-amnezia

Amnezia in the [Omarchy](https://omarchy.org) bar instead of on the desktop: a
shield icon, a switch for on/off, and a list to pick which config it uses.

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

Runs AmneziaWG and plain WireGuard configs. For OpenVPN, XRay/VLESS or
Shadowsocks, keep [the app](https://github.com/amnezia-vpn/amnezia-client).

## 1. Install

```bash
omarchy plugin add https://github.com/rem-aster/omarchy-amnezia.git --enable --yes
```

The widget lands on the right of the bar; `omarchy bar move
rem-aster.amnezia` puts it elsewhere. Everything the plugin needs
lives in its own directory — its scripts are run from there, nothing is placed
on your `PATH`, and no service is installed.

Plugins run unsandboxed inside `omarchy-shell`, so read the code before enabling
one.

### Upgrading

Reload the shell after any update, or the still-loaded old widget keeps calling
files the new checkout has moved:

```bash
omarchy-restart-shell
```

If a previous version left `~/.local/bin/omarchy-amnezia` behind, delete it —
nothing is put on `PATH` any more:

```bash
rm -f ~/.local/bin/omarchy-amnezia
```

**Installed as `io.github.rem-aster.amnezia`?** That was the old id, and the id
is the plugin's directory name, so it has to be reinstalled rather than pulled:

```bash
omarchy plugin remove io.github.rem-aster.amnezia
omarchy plugin add https://github.com/rem-aster/omarchy-amnezia.git --enable --yes
omarchy-restart-shell
```

Your configs are not in the plugin directory — they live in
`~/.config/omarchy/amnezia/` and are untouched by this.

## 2. Get a config out of the Amnezia app

The app always exports AmneziaWG as a `.conf` file:

- **Subscription (Free / Premium):** open the server → **Configuration Files**
  ("for router setup or the AmneziaWG app") → issue one for a country. It saves
  a `.conf`.
- **Your own server:** open the server → **Sharing** → AmneziaWG. Save the
  `.conf`.

A `vpn://` key will not work: it leaves the private key blank for the app to
fill in, so nothing else can use it. The plugin says so if you try.

## 3. Add it

```bash
omarchy-shell amnezia add ~/Downloads/de.conf
omarchy-shell amnezia list
```

Same thing straight from the plugin, when the bar is not running or you want
the output in front of you:

```bash
~/.config/omarchy/plugins/rem-aster.amnezia/scripts/amnezia import ~/Downloads/de.conf
```

A second argument names it: `add ~/Downloads/de.conf germany`. Otherwise the
name comes from the file. It becomes the network interface name, so it is cut
down to 15 characters of `a-z A-Z 0-9 _ = + . -`.

Configs are copied to `~/.config/omarchy/amnezia/configs/` at mode `600`. Adding
a `.conf` to that directory by hand works too — the panel picks it up on its
next refresh.

## 4. Use it

| Where | Action |
| --- | --- |
| Bar icon, left click | open the panel |
| Bar icon, right click | connect / disconnect |
| Panel switch | connect / disconnect |
| Config row, click | make it the one the switch uses |
| Config row, ▶ / ⏸ | connect / disconnect that config |
| `j` `k`, arrows | move the cursor |
| `enter` | pick the row under the cursor |
| `c` | connect the row under the cursor |
| `t` | connect / disconnect |
| `r` | refresh |
| `esc` | close |

One tunnel at a time — connecting another config replaces the first.

## Commands

Everything the plugin does is reachable through its IPC target, for a keybind, a
script, or just a terminal:

```bash
omarchy-shell amnezia status              # what is connected
omarchy-shell amnezia list                # configs, * marks the live one
omarchy-shell amnezia add <file> [name]   # import a .conf
omarchy-shell amnezia remove <name>       # delete a config
omarchy-shell amnezia select <name>       # choose what the switch uses
omarchy-shell amnezia up [name]           # connect
omarchy-shell amnezia down                # disconnect
omarchy-shell amnezia vpnToggle           # connect if off, disconnect if on
omarchy-shell amnezia refresh
omarchy-shell amnezia open / close / toggle    # the panel
```

`add`, `up` and `down` answer straight away and do the work in the background;
the panel shows the result.

The same commands are in `scripts/amnezia` inside the plugin directory, which
is what the widget runs. Calling it directly gives you the full output, plus a
few things the panel has no use for:

```bash
cd ~/.config/omarchy/plugins/rem-aster.amnezia
./scripts/amnezia status              # human-readable, --json for the panel's view
./scripts/amnezia doctor              # what this machine has, and what is missing
./scripts/amnezia backend daemon      # pin how tunnels are raised
./scripts/amnezia edit berlin         # open a config in $EDITOR
./scripts/amnezia rename berlin de    # rename one
```

## Remove it

```bash
omarchy-shell amnezia down            # if a tunnel is up
omarchy plugin disable rem-aster.amnezia
omarchy plugin remove rem-aster.amnezia
rm -rf ~/.config/omarchy/amnezia      # your configs — only if you want them gone
```

That is everything the plugin creates: the plugin checkout and its own directory
under `~/.config/omarchy`. It never edits your `shell.json` itself (enabling and
placing the widget is `omarchy plugin` / `omarchy bar` doing that) and writes
nothing else anywhere.

## Dependencies

The plugin bundles no code and no binaries of its own. It calls what is on the
machine:

| | | |
| --- | --- | --- |
| `bash`, `coreutils` | required | ship with Omarchy |
| the Amnezia app's service | one of these two | [GPL-3.0](https://github.com/amnezia-vpn/amnezia-client) — used over its local socket, nothing installed by us |
| `amneziawg-tools` (+ `amneziawg-dkms`), or `wireguard-tools` | | [GPL-2.0](https://github.com/amnezia-vpn/amneziawg-tools) — `awg-quick` / `wg-quick` |
| `polkit` | with the tools above | for the `pkexec` prompt |
| `openresolv` or `systemd-resolvconf` | with the tools above | only for a config with a `DNS =` line |

So: **if the Amnezia app is installed, nothing to install.** The plugin drives
the service its installer already set up, which asks for no password and brings
its own tunnel binary. Otherwise:

```bash
yay -S amneziawg-tools amneziawg-dkms   # AmneziaWG configs
sudo pacman -S wireguard-tools          # plain WireGuard configs
```

and each connect or disconnect shows one polkit password dialog.

## Settings

On the widget's entry in `~/.config/omarchy/shell.json`:

```json
{ "id": "rem-aster.amnezia", "refreshIntervalSec": 15, "switchWhenConnected": "On" }
```

- **`refreshIntervalSec`** (15) — how often the panel re-reads the state.
- **`switchWhenConnected`** (`On`) — whether picking another config while
  connected moves the tunnel right away, or only decides what connects next.

How tunnels are raised is not a widget setting — it is remembered by the plugin
itself: `./scripts/amnezia backend auto|daemon|quick`.

## Good to know

**Two ways to connect.** With the app installed the plugin sends one `activate`
message to `AmneziaVPN.service` over its socket; otherwise it runs one
`pkexec awg-quick up <config>`. Nothing of the plugin's own runs as root, and it
installs no helper, sudoers entry or polkit rule. The panel's *Via* row says
which one is in use.

**The service is shared with the app.** It runs a single interface, so a tunnel
you started from the app shows up in the panel — the config reads as *not
imported here* when it is one the plugin does not have — and the switch can
still turn it off.

**Security.** A `.conf` holds your private key; configs are written at mode
`600` in a `700` directory. `awg-quick` runs a config's `PreUp`/`PostUp` hooks
as root, so read a config you did not write before adding it. The app's service,
when installed, listens on a socket any local process can command and checks
nothing about the caller — that is what makes it prompt-free, it predates this
plugin, and `backend: quick` opts out of using it. There is no kill switch
either way.

## Troubleshooting

**No icon in the bar, and `omarchy-shell amnezia …` says "Target not found"** —
both mean the widget is not running. Check that it is enabled and placed:

```bash
omarchy plugin list | grep amnezia
omarchy bar move rem-aster.amnezia right
omarchy-restart-shell
```

If it is enabled and still absent, the QML failed to load; the shell's log says
why:

```bash
qs log -p "$OMARCHY_PATH/shell" | grep -i amnezia
qs ipc -p "$OMARCHY_PATH/shell" show | grep -i amnezia   # registered IPC targets
```

**"That is an Amnezia subscription key"** — you passed a `vpn://` key. Export a
`.conf` from the app instead, per step 2.

**"the AmneziaVPN service is not running"** — the app was removed or its service
stopped. `systemctl start AmneziaVPN`, or install the tools above and let the
plugin raise the tunnel itself.

**"the AmneziaVPN service rejected the config"** — the service validates the
config and does not say which field it disliked; `/var/log/AmneziaVPN` does. An
`Endpoint` hostname that no longer resolves is the usual cause.

**"Authorization cancelled"** — the polkit dialog was dismissed. Only the
awg-quick path asks at all.

**`resolvconf: command not found`** — install `openresolv`, or delete the `DNS`
line from the config.

**`Protocol not supported`** — `awg-quick` is installed but the AmneziaWG kernel
module is not. Install `amneziawg-dkms` and reboot.

## Credits

[Amnezia](https://github.com/amnezia-vpn/amnezia-client) and
[amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) for the
protocol and the tooling; [Omarchy](https://github.com/basecamp/omarchy) for the
shell and the first-party widgets this one is patterned after. Not affiliated
with either.

MIT licensed.
