# omarchy-amnezia

Amnezia in the [Omarchy](https://omarchy.org) bar instead of on the desktop: a
shield icon, a switch for on/off, and a list to pick which config it uses. Runs
AmneziaWG and plain WireGuard configs — for OpenVPN, XRay/VLESS or Shadowsocks,
keep [the app](https://github.com/amnezia-vpn/amnezia-client).

```
┌─────────────────────────────────┐
│ 󰒃  Amnezia      AmneziaWG   ●━━ │
│    CONNECTED · 12m              │
│ Config                   berlin │
│ Endpoint    203.0.113.10:35001  │
│ Transfer      ↓ 4.2 MB ↑ 812 KB │
│ ─────────────────────────────── │
│ CONFIGS                         │
│ 󰒃 berlin                   󰄬 󰏤 │
│ 󰒃 amsterdam                  󰐊 │
└─────────────────────────────────┘
```

Left click opens the panel, right click on the icon connects or disconnects.
One tunnel at a time.

## Install

```bash
omarchy plugin add https://github.com/rem-aster/omarchy-amnezia.git --enable --yes
omarchy-restart-shell
```

**If the Amnezia app is installed, that is all** — the plugin drives the service
its installer already set up, and asks for no password. Otherwise install the
tunnel tools, and each connect shows one polkit dialog:

```bash
yay -S amneziawg-tools amneziawg-dkms   # AmneziaWG configs
sudo pacman -S wireguard-tools          # plain WireGuard configs
```

`./scripts/amnezia doctor` in the plugin directory reports what is missing.

## Add a config

The Amnezia app exports AmneziaWG as a `.conf` file — **Configuration Files** on
a subscription server, **Sharing** on your own. Then:

```bash
omarchy-shell amnezia add ~/Downloads/de.conf        # optional second argument names it
omarchy-shell amnezia list
```

A `vpn://` key will not work: it leaves the private key blank for the app to
fill in, so nothing else can use it.

Configs are copied to `~/.config/omarchy/amnezia/configs/` at mode `600`.
Dropping a `.conf` there by hand works too.

## Commands

```bash
omarchy-shell amnezia status | list | up [name] | down | vpnToggle
omarchy-shell amnezia select <name> | remove <name> | refresh
```

The same commands, plus `doctor`, `edit`, `rename` and `backend`, are in
`scripts/amnezia` inside the plugin directory, which is what the widget runs.

## Remove

```bash
omarchy-shell amnezia down
omarchy plugin remove rem-aster.amnezia
rm -rf ~/.config/omarchy/amnezia      # your configs, only if you want them gone
```

Those two directories are everything the plugin creates. Nothing is put on your
`PATH` and no service is installed.

## Notes

Configs hold private keys and are kept at mode `600` in a `700` directory.
`awg-quick` runs a config's `PreUp`/`PostUp` hooks as root, so read a config you
did not write before adding it. The Amnezia app's service, where it is used,
accepts commands from any local process — that is what makes it prompt-free, and
`./scripts/amnezia backend quick` opts out of it. No kill switch either way.

Beyond bash and coreutils the plugin ships nothing and bundles nothing: it calls
the [Amnezia service](https://github.com/amnezia-vpn/amnezia-client) (GPL-3.0)
or [amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) /
`wireguard-tools` (GPL-2.0) through `pkexec`, and `openresolv` when a config
sets `DNS`.

MIT licensed. Not affiliated with Amnezia or Omarchy.
