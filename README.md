# omarchy-amnezia

Amnezia Premium in the [Omarchy](https://omarchy.org) bar. Paste your Premium key once, then pick a location and a protocol — **AmneziaWG** or **VLESS** — and connect, without the Amnezia app's window.

- **Bar icon** — a shield that lights up when connected, with the country code next to it. Left click opens the dashboard, right click connects to your picked location or disconnects, middle click refreshes. The tooltip shows the location, protocol, traffic and uptime.
- **Dashboard** — a terminal UI in a floating window: every location your subscription offers with the protocols it speaks, details of the one under the cursor, and your subscription (valid until, device slots). A second screen lists the devices holding a slot and frees one you no longer use.

```
 Amnezia Premium                                                         v2.0.0
 Connected  🇩🇪 Germany · AmneziaWG · 12m · ↓ 12 MB  ↑ 3.1 MB

 ╭─ Locations · 51 ──────────────────╮  ╭─ Details ───────────────────────────╮
 │      🇫🇮 Finland             AWG  │  │  Status      Connected · 12m         │
 │  › ● 🇩🇪 Germany       AWG VLESS  │  │  Location    🇩🇪 Germany  (de)        │
 │      🇳🇱 Netherlands   AWG VLESS  │  │  Protocol    ▸ AmneziaWG    VLESS    │
 │      🇺🇸 USA (East)        VLESS  │  │  Endpoint    203.0.113.10:47213      │
 ╰───────────────────────────────────╯  ╰──────────────────────────────────────╯
 enter connect • p protocol • / filter • a account • ? help • q quit
```

## Install

```bash
omarchy plugin add https://github.com/rem-aster/omarchy-amnezia.git --enable
```

Then click the icon and paste your key — it starts with `vpn://`; the Amnezia app shows it under your subscription's **Share / Export key**, and it came with the purchase e-mail. Or from a terminal:

```bash
~/.config/omarchy/plugins/rem-aster.amnezia/scripts/amnezia key 'vpn://…'
```

What each protocol needs on the machine:

| Protocol | Needs | Password prompt |
|---|---|---|
| AmneziaWG | the [AmneziaVPN app](https://github.com/amnezia-vpn/amnezia-client) installed — the plugin uses the service it leaves running | none |
| AmneziaWG, without the app | `amneziawg-tools` + `amneziawg-dkms` (AUR), new enough for the AWG 2/3 options Premium configs use | each connect |
| VLESS | `xray` (`sudo pacman -S xray`), a release with the TUN inbound | each connect and disconnect |

`scripts/amnezia doctor` checks all of it.

## Using it

In the dashboard: **enter** connects to the location under the cursor (or disconnects it), **p** switches between AmneziaWG and VLESS — a live tunnel moves over right away — **/** filters, **R** asks for a fresh config, **a** opens the account screen (**x** frees a device slot, **o** opens the renewal page, **k** replaces the key). **?** lists everything; **q** closes the window and leaves the tunnel up.

The same from a terminal (`scripts/amnezia` in the plugin directory):

```bash
amnezia locations              # codes and protocols: de, nl, us-east…
amnezia up nl vless            # connect (no arguments: the picked location)
amnezia use fi                 # pick another location; a live tunnel follows
amnezia protocol awg
amnezia down
amnezia status
amnezia account | devices | revoke <id> | renew | renewal | export <cc> | forget
```

`omarchy-shell amnezia open | toggle | refresh | status` reaches the widget.

## How it works

The plugin speaks to Amnezia's API gateway the way the app's `GatewayController` does — the request is AES-256 encrypted under a one-time key that is wrapped with the gateway's RSA public key (`scripts/amnezia_lib/gateway-public.pem`, the one every AmneziaVPN build carries). When `gw.amnezia.org` is unreachable it falls back to the same mirror gateways the app uses. Everything is in Python's standard library; nothing to install for that part.

Like the app, the plugin is one **device** of your subscription: it makes an installation id once and reuses it for every location and protocol, so it takes one slot (two if the app also runs on this machine). Configs are issued per location and protocol on first use and kept; **R** replaces one. A brand-new config can take a minute or two before the server lets traffic through.

- **AmneziaWG** goes to the AmneziaVPN service over its local socket when the app is installed; that service runs as root and accepts commands from any local process, which is what makes it prompt-free. Without it, `awg-quick` runs through `pkexec`. `amnezia backend quick` forces the second.
- **VLESS** runs `xray` with a TUN inbound as a transient systemd unit (`omarchy-amnezia-vless`), started through `pkexec` by `scripts/amnezia-root`. Everything except the server goes into the tunnel, DNS points into it (systemd-resolved or resolvconf), and IPv6 is blocked while it is up so nothing leaks around it. Stopping the unit undoes all of that.

One tunnel at a time; connecting one takes the other down. There is no kill switch.

## Files

| Path | What |
|---|---|
| `~/.config/omarchy/amnezia/` | the key, this device's id, your picks, the configs Amnezia issued — mode `600` in a `700` directory |
| `$XDG_RUNTIME_DIR/omarchy-amnezia/` | what is up right now, and the config handed to the tunnel |
| `/run/omarchy-amnezia/` | root's copy of that config while a VLESS or `awg-quick` tunnel runs |
| `~/.local/state/omarchy-amnezia/amnezia.log` | what was done and what failed — never keys |

The plugin never edits `shell.json` and never installs anything.

## Remove

```bash
~/.config/omarchy/plugins/rem-aster.amnezia/scripts/amnezia down
~/.config/omarchy/plugins/rem-aster.amnezia/scripts/amnezia forget   # frees this device's slot
omarchy plugin remove rem-aster.amnezia
rm -rf ~/.config/omarchy/amnezia ~/.local/state/omarchy-amnezia
```

## Dependencies

Nothing third-party is bundled. The plugin calls, when present:

| Tool | Used for | License |
|---|---|---|
| python3 | everything in `scripts/` | PSF |
| AmneziaVPN app (its service) | AmneziaWG without a password | GPL-3.0 |
| amneziawg-tools | AmneziaWG through `awg-quick` | GPL-2.0 |
| xray | VLESS | MPL-2.0 |
| polkit (`pkexec`) | the root steps | LGPL-2.0 |
| systemd (`systemd-run`, `resolvectl`), iproute2 | the VLESS tunnel's unit, DNS and routes | LGPL-2.1 / GPL-2.0 |

Tests (no network, no root): `cd tests && python3 -B -m unittest`.

Upgrading from 1.x: plain `.conf` configs are no longer used; the old `~/.config/omarchy/amnezia/configs/` can be deleted.

MIT license
