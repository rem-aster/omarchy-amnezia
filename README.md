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
io.github.rem-aster.amnezia` puts it elsewhere. On first run it symlinks its
`omarchy-amnezia` command into `~/.local/bin`, so the commands below work in any
terminal.

Plugins run unsandboxed inside `omarchy-shell`, so read the code before enabling
one.

## 2. Get a config out of the Amnezia app

The app always exports AmneziaWG as a `.conf` file:

- **Subscription (Free / Premium):** open the server → **Configuration Files**
  ("for router setup or the AmneziaWG app") → issue one for a country. It saves
  a `.conf`.
- **Your own server:** open the server → **Sharing** → AmneziaWG. Save the
  `.conf`.

Do not paste a `vpn://` key — those leave the private key blank for the app to
fill in, so nothing else can use them. The import will tell you as much.

## 3. Import it

```bash
omarchy-amnezia import ~/Downloads/de.conf --name germany
omarchy-amnezia list
```

`--name` is optional; without it the name comes from the file. It becomes the
network interface name, so keep it under 16 characters of `a-z A-Z 0-9 _ = + . -`.

Configs live in `~/.config/omarchy/amnezia/configs/` at mode `600`. Dropping a
`.conf` in there by hand works too — the panel picks it up on its next refresh.

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

For a keybind or a script:

```bash
omarchy-shell amnezia vpnToggle       # connect / disconnect
omarchy-shell amnezia vpnUp berlin    # connect a named config
omarchy-shell amnezia vpnDown
```

## Remove it

```bash
omarchy-amnezia down                 # if a tunnel is up
omarchy-amnezia unlink               # the ~/.local/bin symlink
omarchy plugin disable io.github.rem-aster.amnezia
omarchy plugin remove io.github.rem-aster.amnezia
rm -rf ~/.config/omarchy/amnezia     # your configs — only if you want them gone
```

Those are everything the plugin creates: the plugin checkout, one symlink, and
its own directory under `~/.config/omarchy`. It never edits your `shell.json`
by itself (enabling and placing the widget is `omarchy plugin` / `omarchy bar`
doing that), and it writes nothing else outside those paths.

## Dependencies

The plugin bundles no third-party code and no binaries. It calls what is on the
machine:

| | | |
| --- | --- | --- |
| `bash`, `jq`, `python3` | required | ship with Omarchy |
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

`omarchy-amnezia doctor` says which mode is in force and what is missing.

## Settings

On the widget's entry in `~/.config/omarchy/shell.json`:

```json
{ "id": "io.github.rem-aster.amnezia", "refreshIntervalSec": 15, "switchWhenConnected": "On" }
```

- **`refreshIntervalSec`** (15) — how often the panel re-reads the state.
- **`switchWhenConnected`** (`On`) — whether picking another config while
  connected moves the tunnel right away, or only decides what connects next.

## Commands

```
omarchy-amnezia status               what is connected
omarchy-amnezia list                 list configs
omarchy-amnezia up [name]            connect
omarchy-amnezia down                 disconnect
omarchy-amnezia toggle [name]        connect if off, disconnect if on
omarchy-amnezia select <name>        choose what the switch uses
omarchy-amnezia import <file>        import a .conf (or an exported .vpn/.json)
omarchy-amnezia remove <name>        delete a config
omarchy-amnezia rename <old> <new>   rename a config
omarchy-amnezia edit [name]          open a config in $EDITOR
omarchy-amnezia backend [name]       auto | daemon | quick
omarchy-amnezia link / unlink        the ~/.local/bin symlink
omarchy-amnezia doctor               check this machine
```

Add `--json` to `status` and `list` for the data the panel reads.

## Good to know

**Two ways to connect, chosen per run.** With the app installed the plugin sends
one `activate` message to `AmneziaVPN.service`; otherwise it runs one
`pkexec awg-quick up <config>`. Nothing of the plugin's own runs as root, and it
installs no helper, sudoers entry or polkit rule. `omarchy-amnezia backend
daemon|quick` pins the choice.

**The service is shared with the app.** It runs a single interface, so a tunnel
you started from the app shows up in the panel — marked *not imported here* when
it is a config the plugin does not have — and the switch can still turn it off.

**Security.** A `.conf` holds your private key; configs stay at mode `600` in a
`700` directory. `awg-quick` runs a config's `PreUp`/`PostUp` hooks as root, so
read a config you did not write before importing it (`omarchy-amnezia edit`).
The app's service, when installed, listens on a socket any local process can
command and checks nothing about the caller — that is what makes it
prompt-free, it predates this plugin, and `backend quick` opts out of using it.
There is no kill switch either way.

## Troubleshooting

**"This is an Amnezia subscription key"** — you pasted a `vpn://` key. Export a
`.conf` from the app instead, per step 2.

**"the AmneziaVPN service is not running"** — the app was removed or its service
stopped. `systemctl start AmneziaVPN`, or install the tools above and let the
plugin do it itself.

**"the AmneziaVPN service rejected the config"** — the service validates the
config and does not say which field it disliked; `/var/log/AmneziaVPN` does. An
`Endpoint` hostname that no longer resolves is the usual cause.

**`resolvconf: command not found`** — install `openresolv`, or drop the `DNS`
line with `omarchy-amnezia edit <name>`.

**`Protocol not supported`** — `awg-quick` is installed but the AmneziaWG kernel
module is not. Install `amneziawg-dkms` and reboot.

**A config will not connect** — run `omarchy-amnezia up <name>` in a terminal;
the underlying tool prints what it refused.

## Credits

[Amnezia](https://github.com/amnezia-vpn/amnezia-client) and
[amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) for the
protocol and the tooling; [Omarchy](https://github.com/basecamp/omarchy) for the
shell and the first-party widgets this one is patterned after. Not affiliated
with either.

MIT licensed.
