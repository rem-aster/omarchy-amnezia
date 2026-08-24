// Parsing and formatting for the Amnezia widget. Everything here is pure
// functions over strings, so the QML files stay about state and layout.

// awg-quick names the interface after the config file and only accepts these,
// so a config name is held to the same rule everywhere.
var NAME_PATTERN = /^[A-Za-z0-9_=+.-]{1,15}$/

// Lines the store script prints around each config it dumps. Deliberately
// unlikely to appear in a real .conf.
var CONF_MARKER = "###amnezia:conf###"
var STATE_MARKER = "###amnezia:state###"

// AmneziaWG is WireGuard plus obfuscation knobs; the presence of any of them
// is what decides which of the two tools can run a config, and the keys are
// named identically in the .conf and in the service's activate payload
// (configKeys.h: awgProtocolKeys()).
var AWG_KEYS = [
  "jc", "jmin", "jmax",
  "s1", "s2", "s3", "s4",
  "h1", "h2", "h3", "h4",
  "i1", "i2", "i3", "i4", "i5",
  "headerprotectionkey", "contentpaddingaddition",
  "rekeyaftertime", "rekeytimeout", "rejectaftertime",
  "keepalivetimeout", "maxhandshakeattempts",
  "randomtrailers", "disablecookies"
]

var AWG_PAYLOAD_KEYS = [
  "Jc", "Jmin", "Jmax",
  "S1", "S2", "S3", "S4",
  "H1", "H2", "H3", "H4",
  "I1", "I2", "I3", "I4", "I5",
  "HeaderProtectionKey", "ContentPaddingAddition",
  "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime",
  "KeepaliveTimeout", "MaxHandshakeAttempts",
  "RandomTrailers", "DisableCookies"
]

// The value the app hardcodes for every tunnel: a non-routable ULA, so the OS
// treats the IPv6 link as local and falls back to IPv4 instead of retrying
// forever (the comment in LocalSocketController::activate explains why).
var DEVICE_IPV6 = "fd58:baa6:dead::1"

function validName(name) {
  var value = String(name || "")
  return NAME_PATTERN.test(value) && value.indexOf("..") === -1 && value.charAt(0) !== "."
}

// A file name from the outside world becomes a name awg-quick would accept.
function sanitizeName(name, fallback) {
  var slug = String(name || "").toLowerCase().replace(/\.conf$/, "")
  slug = slug.replace(/[^A-Za-z0-9_=+.-]+/g, "-").replace(/-{2,}/g, "-")
  slug = slug.replace(/^[-.]+/, "").replace(/[-.]+$/, "").substring(0, 15)
  slug = slug.replace(/^[-.]+/, "").replace(/[-.]+$/, "")
  return slug !== "" ? slug : String(fallback || "amnezia")
}

// [Interface] / [Peer] into two maps of lowercased key → value. First value
// wins, comments and blank lines dropped.
function parseConf(text) {
  var sections = { "interface": {}, peer: {} }
  var current = ""
  var lines = String(text || "").split("\n")
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].split("#")[0].trim()
    if (line === "") continue
    if (line.charAt(0) === "[" && line.charAt(line.length - 1) === "]") {
      current = line.substring(1, line.length - 1).trim().toLowerCase()
      continue
    }
    if (current !== "interface" && current !== "peer") continue
    var eq = line.indexOf("=")
    if (eq <= 0) continue
    var key = line.substring(0, eq).trim().toLowerCase()
    var value = line.substring(eq + 1).trim()
    if (value === "" || sections[current][key] !== undefined) continue
    sections[current][key] = value
  }
  return sections
}

function isConf(text) {
  var lowered = String(text || "").toLowerCase()
  return lowered.indexOf("[interface]") !== -1 && lowered.indexOf("[peer]") !== -1
}

function protocolOf(conf) {
  var iface = conf["interface"] || {}
  for (var i = 0; i < AWG_KEYS.length; i++) {
    if (iface[AWG_KEYS[i]] !== undefined) return "awg"
  }
  return "wireguard"
}

// The fields the panel shows, out of an already-parsed config.
function confFields(conf) {
  var iface = conf["interface"] || {}
  var peer = conf.peer || {}
  return {
    protocol: protocolOf(conf),
    endpoint: String(peer.endpoint || ""),
    address: String(iface.address || ""),
    dns: String(iface.dns || ""),
    mtu: String(iface.mtu || "")
  }
}

function endpointHost(endpoint) {
  var value = String(endpoint || "").trim()
  if (value === "") return ""
  if (value.charAt(0) === "[") return value.substring(1, value.indexOf("]"))
  var colon = value.lastIndexOf(":")
  return colon === -1 ? value : value.substring(0, colon)
}

function endpointPort(endpoint) {
  var value = String(endpoint || "").trim()
  var port = ""
  if (value.charAt(0) === "[") {
    port = value.substring(value.indexOf("]") + 1).replace(/^:/, "")
  } else {
    var colon = value.lastIndexOf(":")
    port = colon === -1 ? "" : value.substring(colon + 1)
  }
  var parsed = parseInt(port, 10)
  return isFinite(parsed) ? parsed : 0
}

function isIpv4(value) {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(value || "").trim())
}

function firstIpv4(value) {
  var parts = String(value || "").split(",")
  for (var i = 0; i < parts.length; i++) {
    var entry = parts[i].trim()
    if (entry !== "" && entry.indexOf(":") === -1) return entry
  }
  return ""
}

function stripMask(value) {
  return String(value || "").split("/")[0].trim()
}

function dnsServers(value) {
  var out = []
  var parts = String(value || "").split(",")
  for (var i = 0; i < parts.length; i++) {
    var entry = parts[i].trim()
    if (entry !== "") out.push(entry)
  }
  return out
}

// ------------------------------------------------------- the store's output
//
// One `bash -c` prints every config plus the live interface state, and this
// turns that into the model the panel binds to. Doing it in one pass keeps a
// refresh to a single process no matter how many configs there are.

function parseStore(blob) {
  var result = {
    ok: false,
    socket: "",
    tools: { awgQuick: false, wgQuick: false, pkexec: false },
    configs: []
  }
  var text = String(blob || "")
  if (text.trim() === "") return result

  result.ok = true
  var current = null
  var body = []
  var lines = text.split("\n")

  function flush() {
    if (current === null) return
    var conf = parseConf(body.join("\n"))
    var fields = confFields(conf)
    // A file with no keys is not a config; listing it would only produce a row
    // that fails on click.
    if (conf["interface"].privatekey && conf.peer.publickey) {
      current.protocol = fields.protocol
      current.endpoint = fields.endpoint
      current.address = fields.address
      current.dns = fields.dns
      current.mtu = fields.mtu
      result.configs.push(current)
    }
    current = null
    body = []
  }

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i]
    if (line.indexOf(STATE_MARKER) === 0) {
      flush()
      var state = line.substring(STATE_MARKER.length).trim().split(/\s+/)
      if (state[0] === "socket" && state.length > 1) result.socket = state[1]
      if (state[0] === "tools") {
        result.tools = {
          awgQuick: state.indexOf("awg-quick") !== -1,
          wgQuick: state.indexOf("wg-quick") !== -1,
          pkexec: state.indexOf("pkexec") !== -1
        }
      }
      continue
    }
    if (line.indexOf(CONF_MARKER) === 0) {
      flush()
      var head = line.substring(CONF_MARKER.length).trim().split(/\s+/)
      if (!validName(head[0])) continue
      current = {
        name: head[0],
        up: head[1] === "up",
        rxBytes: Number(head[2] || 0),
        txBytes: Number(head[3] || 0),
        protocol: "",
        endpoint: "",
        address: "",
        dns: "",
        mtu: ""
      }
      continue
    }
    if (current !== null) body.push(line)
  }
  flush()

  result.configs.sort(function(a, b) { return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0) })
  return result
}

function findConfig(configs, name) {
  for (var i = 0; i < configs.length; i++) {
    if (configs[i].name === String(name)) return configs[i]
  }
  return null
}

// ------------------------------------------------------------ the service
//
// The activate payload the AmneziaVPN service validates field by field
// (Daemon::parseConfig). Mirrors LocalSocketController::activate() — the same
// key names, the same types, the same hardcoded ULA — because anything else
// is refused with no reason given.

function allowedRanges(value) {
  var ranges = []
  var parts = String(value || "").split(",")
  for (var i = 0; i < parts.length; i++) {
    var entry = parts[i].trim()
    if (entry === "") continue
    var slash = entry.indexOf("/")
    var address = (slash === -1 ? entry : entry.substring(0, slash)).trim()
    var isIpv6 = address.indexOf(":") !== -1
    var length = slash === -1 ? (isIpv6 ? 128 : 32) : parseInt(entry.substring(slash + 1), 10)
    if (!isFinite(length)) continue
    ranges.push({ address: address, range: length, isIpv6: isIpv6 })
  }
  if (ranges.length === 0) {
    ranges = [
      { address: "0.0.0.0", range: 0, isIpv6: false },
      { address: "::", range: 0, isIpv6: true }
    ]
  }
  return ranges
}

// `serverIp` has to be an address: the service feeds it to IPAddress() for the
// exclusion route and to the tunnel's UAPI endpoint, neither of which resolves
// a name.
function activatePayload(confText, serverIp) {
  var conf = parseConf(confText)
  var iface = conf["interface"] || {}
  var peer = conf.peer || {}

  var address = firstIpv4(iface.address)
  var port = endpointPort(peer.endpoint)
  if (!iface.privatekey) return { error: "the config has no PrivateKey" }
  if (!peer.publickey) return { error: "the config has no PublicKey" }
  if (address === "") return { error: "the config has no IPv4 Address" }
  if (port <= 0) return { error: "the config's Endpoint has no port" }
  if (!isIpv4(serverIp)) return { error: "could not resolve " + endpointHost(peer.endpoint) }

  var payload = {
    type: "activate",
    privateKey: iface.privatekey,
    serverPublicKey: peer.publickey,
    serverPskKey: peer.presharedkey || peer["preshared-key"] || "",
    serverPort: port,
    deviceIpv4Address: address,
    deviceIpv6Address: DEVICE_IPV6,
    serverIpv4AddrIn: serverIp,
    serverIpv4Gateway: serverIp,
    allowedIPAddressRanges: allowedRanges(peer.allowedips),
    // Traffic to the endpoint must stay off the tunnel, or the tunnel routes
    // its own packets. The app excludes the same address.
    excludedAddresses: [serverIp],
    vpnDisabledApps: [],
    allowedDnsServers: [],
    killSwitchOption: "false"
  }

  // deviceMTU is read as `value.toString().toInt()`, so a JSON number reads as
  // 0 and silently falls back to 1420.
  if (iface.mtu) payload.deviceMTU = String(iface.mtu)
  if (peer.persistentkeepalive) payload.persistentKeepalive = String(peer.persistentkeepalive)

  var servers = dnsServers(iface.dns)
  if (servers.length > 0) payload.primaryDnsServer = servers[0]
  if (servers.length > 1) payload.secondaryDnsServer = servers[1]

  for (var i = 0; i < AWG_PAYLOAD_KEYS.length; i++) {
    var value = iface[AWG_PAYLOAD_KEYS[i].toLowerCase()]
    if (value) payload[AWG_PAYLOAD_KEYS[i]] = String(value)
  }

  return { payload: payload }
}

// ------------------------------------------------------------- formatting

function protocolLabel(protocol) {
  if (String(protocol) === "awg") return "AmneziaWG"
  if (String(protocol) === "wireguard") return "WireGuard"
  return ""
}

function backendLabel(backend) {
  if (String(backend) === "daemon") return "AmneziaVPN service"
  if (String(backend) === "quick") return "awg-quick"
  return ""
}

// The tool a config needs is decided by the config. The service brings its own
// tunnel binary, so in that mode there is nothing on the box to be missing.
function missingToolFor(config, backend, tools) {
  if (!config || String(backend) === "daemon") return ""
  if (!tools) return ""
  if (String(config.protocol) === "awg") return tools.awgQuick ? "" : "needs amneziawg-tools"
  return tools.wgQuick || tools.awgQuick ? "" : "needs wireguard-tools"
}

function configMeta(config, backend, tools) {
  if (!config) return ""
  var parts = []
  var label = protocolLabel(config.protocol)
  if (label !== "") parts.push(label)
  if (config.endpoint) parts.push(config.endpoint)
  var missing = missingToolFor(config, backend, tools)
  if (missing !== "") parts.push(missing)
  return parts.join(" · ")
}

function formatBytes(bytes) {
  var value = Number(bytes || 0)
  if (!isFinite(value) || value <= 0) return "0 B"
  var units = ["B", "KB", "MB", "GB", "TB"]
  var index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index++
  }
  return value.toFixed(value < 10 && index > 0 ? 1 : 0) + " " + units[index]
}

function transferText(rxBytes, txBytes) {
  return "↓ " + formatBytes(rxBytes) + "   ↑ " + formatBytes(txBytes)
}

function uptimeText(sinceMs, nowMs) {
  var since = Number(sinceMs || 0)
  if (!isFinite(since) || since <= 0) return ""
  var seconds = Math.max(0, Math.floor((Number(nowMs) - since) / 1000))
  if (seconds < 60) return seconds + "s"
  var minutes = Math.floor(seconds / 60)
  if (minutes < 60) return minutes + "m"
  var hours = Math.floor(minutes / 60)
  if (hours < 24) return hours + "h " + (minutes % 60) + "m"
  var days = Math.floor(hours / 24)
  return days + "d " + (hours % 24) + "h"
}

function heroMeta(state, nowMs) {
  if (state.busy) return "Working"
  if (!state.connected) return state.configCount === 0 ? "No configs" : "Disconnected"
  if (state.activeName === "") return "Connected · another config"
  var uptime = uptimeText(state.since, nowMs)
  return uptime === "" ? "Connected" : "Connected · " + uptime
}

// pkexec exits 126 when its dialog is dismissed and 127 when it cannot start
// at all; both read as gibberish next to a switch.
function commandError(exitCode, stderr, stdout) {
  var text = String(stderr || "").trim()
  if (text === "") text = String(stdout || "").trim()
  if (exitCode === 126 || /dismissed|not authorized|Authentication failed/i.test(text)) {
    return "Authorization cancelled"
  }
  if (exitCode === 127 && text === "") return "pkexec is missing"
  text = text.replace(/\s+/g, " ").trim()
  if (text === "") return "Command failed (exit " + exitCode + ")"
  return text.length > 160 ? text.substring(0, 157) + "…" : text
}

// The one import failure worth a bespoke message: a subscription key leaves the
// private key for the app to fill in, so nothing else can complete it.
function importError(text) {
  var body = String(text || "").trim()
  if (/^(vpn|amnezia):\/\//i.test(body)) {
    return "That is an Amnezia subscription key, not a config. The app fills in "
      + "its private key itself, so nothing else can use it — export a .conf "
      + "from the app instead (open the server, then Configuration Files)."
  }
  if (/=\s*\$[A-Z_]/.test(body)) {
    return "This config still has $PLACEHOLDERS instead of keys, so it is a "
      + "template. Export a finished .conf from the Amnezia app."
  }
  if (!isConf(body)) {
    if (body.charAt(0) === "{") return "That is a JSON config. Export a .conf from the Amnezia app instead."
    if (/dev tun|dev tap/.test(body)) return "That is an OpenVPN config. This plugin runs AmneziaWG and WireGuard."
    return "That is not a WireGuard or AmneziaWG .conf."
  }
  var conf = parseConf(body)
  if (!conf["interface"].privatekey) return "The config has no PrivateKey."
  if (!conf.peer.publickey) return "The config has no PublicKey."
  if (endpointPort(conf.peer.endpoint) <= 0) return "The config has no Endpoint with a port."
  return ""
}
