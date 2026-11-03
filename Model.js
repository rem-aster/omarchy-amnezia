.pragma library

// Pure parsing and formatting for the Amnezia panel. Everything here takes
// the JSON `scripts/amnezia state` prints and turns it into what the panel
// shows, so the QML stays about layout and state.

// The script bounds its own answer; the panel does not take its word for it.
var maxStateBytes = 262144
var maxCountries = 120
var maxDevices = 32
var maxText = 160

var protocols = ["awg", "vless"]
var protocolLabels = { awg: "AmneziaWG", vless: "VLESS" }

function clip(value, limit) {
  var text = String(value === undefined || value === null ? "" : value)
  var max = limit || maxText
  return text.length > max ? text.substring(0, max) : text
}

function num(value) {
  var n = Number(value || 0)
  return isFinite(n) ? n : 0
}

function emptyState() {
  return {
    ok: false, hasKey: false, busy: false, connected: false, foreign: false,
    country: "", countryName: "", flag: "", protocol: "awg", backend: "", backendLabel: "",
    rx: 0, tx: 0, since: 0, iface: "", endpoint: "", daysLeft: null,
    plan: "", description: "", picked: { country: "", protocol: "awg" },
    awgBackend: "", awgBackendSetting: "auto", awgModule: "", awgGo: false, xray: false, pkexec: false, deviceId: "",
    account: { known: false, fetchedAt: 0, endDate: "", activeDevices: 0, maxDevices: 0, renewal: false, support: {} },
    countries: [], devices: [], recent: [], configs: {}
  }
}

function protocolOf(value) {
  return protocols.indexOf(String(value)) >= 0 ? String(value) : "awg"
}

function parseState(raw) {
  var text = String(raw || "").trim()
  if (text === "" || text.length > maxStateBytes) return null
  var parsed
  try { parsed = JSON.parse(text) } catch (error) { return null }
  if (!parsed || typeof parsed !== "object" || parsed.ok !== true) return null

  var s = emptyState()
  s.ok = true
  s.hasKey = parsed.hasKey === true
  s.busy = parsed.busy === true
  s.connected = parsed.connected === true
  s.foreign = parsed.foreign === true
  s.country = clip(parsed.country, 24)
  s.countryName = clip(parsed.countryName, 64)
  s.flag = clip(parsed.flag, 8)
  s.protocol = protocolOf(parsed.protocol)
  s.backend = clip(parsed.backend, 16)
  s.backendLabel = clip(parsed.backendLabel, 40)
  s.rx = num(parsed.rx)
  s.tx = num(parsed.tx)
  s.since = num(parsed.since)
  s.iface = /^[A-Za-z0-9_.-]{1,15}$/.test(String(parsed.iface || "")) ? String(parsed.iface) : ""
  s.endpoint = clip(parsed.endpoint, 120)
  s.daysLeft = parsed.daysLeft === null || parsed.daysLeft === undefined ? null : num(parsed.daysLeft)
  s.plan = clip(parsed.plan, 64)
  s.description = clip(parsed.description, 128)
  var picked = parsed.picked && typeof parsed.picked === "object" ? parsed.picked : {}
  s.picked = { country: clip(picked.country, 24), protocol: protocolOf(picked.protocol) }
  s.awgBackend = clip(parsed.awgBackend, 16)
  s.awgBackendSetting = clip(parsed.awgBackendSetting, 16)
  s.awgModule = clip(parsed.awgModule, 16)
  s.awgGo = parsed.awgGo === true
  s.xray = parsed.xray === true
  s.pkexec = parsed.pkexec === true
  s.deviceId = clip(parsed.deviceId, 36)

  var account = parsed.account && typeof parsed.account === "object" ? parsed.account : {}
  var support = {}
  if (account.support && typeof account.support === "object") {
    var keys = ["email", "telegram", "website"]
    for (var k = 0; k < keys.length; k++) {
      if (typeof account.support[keys[k]] === "string") support[keys[k]] = clip(account.support[keys[k]], 120)
    }
  }
  s.account = {
    known: account.known === true,
    fetchedAt: num(account.fetchedAt),
    endDate: clip(account.endDate, 10),
    activeDevices: num(account.activeDevices),
    maxDevices: num(account.maxDevices),
    renewal: account.renewal === true,
    support: support
  }

  var countries = Array.isArray(parsed.countries) ? parsed.countries.slice(0, maxCountries) : []
  for (var i = 0; i < countries.length; i++) {
    var c = countries[i]
    if (!c || typeof c !== "object" || !/^[a-z]{2}(-[a-z0-9]{1,12})?$/.test(String(c.code || ""))) continue
    var offered = []
    var list = Array.isArray(c.protocols) ? c.protocols : []
    for (var j = 0; j < list.length; j++) if (protocols.indexOf(list[j]) >= 0 && offered.indexOf(list[j]) < 0) offered.push(list[j])
    s.countries.push({ code: String(c.code), name: clip(c.name, 64) || String(c.code).toUpperCase(),
                       flag: clip(c.flag, 8), protocols: offered })
  }

  var devices = Array.isArray(parsed.devices) ? parsed.devices.slice(0, maxDevices) : []
  for (var d = 0; d < devices.length; d++) {
    var dev = devices[d]
    if (!dev || typeof dev !== "object") continue
    s.devices.push({
      uuid: /^[0-9a-fA-F-]{36}$/.test(String(dev.uuid || "")) ? String(dev.uuid) : "",
      os: clip(dev.os, 32), country: clip(dev.country, 16), countryName: clip(dev.country_name, 48),
      source: clip(dev.source, 32), last: num(dev.last), me: dev.me === true
    })
  }

  var recent = Array.isArray(parsed.recent) ? parsed.recent.slice(0, 3) : []
  for (var r = 0; r < recent.length; r++) {
    if (recent[r] && typeof recent[r] === "object" && typeof recent[r].country === "string")
      s.recent.push({ country: clip(recent[r].country, 24), protocol: protocolOf(recent[r].protocol) })
  }

  if (parsed.configs && typeof parsed.configs === "object") {
    var names = Object.keys(parsed.configs).slice(0, maxCountries * 2)
    for (var n = 0; n < names.length; n++) {
      var cfg = parsed.configs[names[n]]
      if (!cfg || typeof cfg !== "object") continue
      s.configs[clip(names[n], 40)] = {
        endpoint: clip(cfg.endpoint, 120), address: clip(cfg.address, 64),
        transport: clip(cfg.transport, 40), dns: clip(cfg.dns, 80), issuedAt: num(cfg.issuedAt)
      }
    }
  }
  return s
}

// ------------------------------------------------------------------ lookup

function protocolLabel(protocol) {
  return protocolLabels[String(protocol)] || String(protocol || "").toUpperCase()
}

function country(state, code) {
  var list = state ? state.countries : []
  for (var i = 0; i < list.length; i++) if (list[i].code === code) return list[i]
  return null
}

function placeName(entry, code) {
  if (!entry) return String(code || "").toUpperCase()
  return (entry.flag !== "" ? entry.flag + " " : "") + entry.name
}

function offers(entry, protocol) {
  return !entry || entry.protocols.length === 0 || entry.protocols.indexOf(protocol) >= 0
}

// The protocol a location will be reached over: the one picked when the
// location speaks it, else the first it has.
function protocolFor(entry, picked) {
  if (offers(entry, picked)) return picked
  return entry.protocols[0]
}

function filterCountries(list, filter) {
  var needle = String(filter || "").trim().toLowerCase()
  if (needle === "") return list
  var out = []
  for (var i = 0; i < list.length; i++) {
    if (list[i].name.toLowerCase().indexOf(needle) >= 0 || list[i].code.indexOf(needle) === 0) out.push(list[i])
  }
  return out
}

function recentRows(recent, countries) {
  var out = []
  for (var i = 0; i < recent.length; i++) {
    for (var j = 0; j < countries.length; j++) {
      if (countries[j].code === recent[i].country) {
        out.push({ entry: countries[j], protocol: recent[i].protocol })
        break
      }
    }
  }
  return out
}

function config(state, code, protocol) {
  return state.configs[code + "-" + protocol] || null
}

// ------------------------------------------------------------------ format

function formatBytes(bytes) {
  var value = num(bytes)
  if (value <= 0) return "0 B"
  var units = ["B", "KB", "MB", "GB", "TB"]
  var index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++ }
  return value.toFixed(value < 10 && index > 0 ? 1 : 0) + " " + units[index]
}

function formatRate(bytesPerSecond) {
  return formatBytes(bytesPerSecond) + "/s"
}

function formatDuration(seconds) {
  var s = Math.max(0, Math.floor(num(seconds)))
  if (s < 60) return s + "s"
  var m = Math.floor(s / 60)
  if (m < 60) return m + "m"
  var h = Math.floor(m / 60)
  if (h < 24) return h + "h " + (m % 60) + "m"
  return Math.floor(h / 24) + "d " + (h % 24) + "h"
}

function formatAgo(epochSeconds, nowMs) {
  var at = num(epochSeconds)
  if (at <= 0) return ""
  var seconds = Math.max(0, Math.floor(num(nowMs) / 1000 - at))
  if (seconds < 60) return "just now"
  if (seconds < 3600) return Math.floor(seconds / 60) + "m ago"
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago"
  return Math.floor(seconds / 86400) + "d ago"
}

function uptime(state, nowMs) {
  return state.since > 0 ? formatDuration(num(nowMs) / 1000 - state.since) : ""
}

var osNames = { ios: "iOS", macos: "macOS", osx: "macOS", android: "Android", windows: "Windows",
                linux: "Linux", arch: "Arch Linux", ubuntu: "Ubuntu", debian: "Debian", fedora: "Fedora" }
var sourceNames = { gateway_account: "app", native_config: "config file", native: "config file" }

function deviceName(device) {
  if (device.me) return "This computer"
  var os = String(device.os || "")
  return osNames[os.toLowerCase()] || (os !== "" ? os.charAt(0).toUpperCase() + os.substring(1) : "Device")
}

function deviceDetail(device, nowMs) {
  var parts = []
  var place = device.countryName !== "" ? device.countryName : device.country.toUpperCase()
  if (place !== "") parts.push(place)
  var source = sourceNames[device.source] || device.source.replace(/_/g, " ")
  if (source !== "") parts.push(source)
  var ago = formatAgo(device.last, nowMs)
  if (ago !== "") parts.push(ago)
  return parts.join(" · ")
}

function protocolsLine(entry) {
  var labels = []
  for (var i = 0; i < entry.protocols.length; i++) labels.push(protocolLabel(entry.protocols[i]))
  return labels.join(" · ")
}

// What raises a protocol on this machine, for the line under the switcher.
// `level` is "ok", "warn" (works, with a cost) or "missing".
function protocolNote(state, protocol) {
  if (protocol === "vless") {
    if (!state.xray) return { level: "missing", text: "VLESS needs xray — sudo pacman -S xray" }
    return { level: "warn", text: "VLESS runs Xray and asks for your password to connect and disconnect" }
  }
  if (state.awgBackend === "daemon") return { level: "ok", text: "AmneziaWG through the AmneziaVPN service — no password" }
  if (state.awgBackend === "quick") {
    if (state.awgModule === "" && !state.awgGo)
      return { level: "missing", text: "awg-quick has nothing to make the interface with: the amneziawg module is not built "
        + "for this kernel (reinstall amneziawg-dkms, reboot) and amneziawg-go is not installed" }
    return { level: "warn", text: "AmneziaWG through awg-quick — asks for your password" }
  }
  return { level: "missing", text: "AmneziaWG needs the AmneziaVPN app, or amneziawg-tools" }
}

function heroMeta(state, busyLabel, nowMs) {
  if (!state.ok) return "Reading the state…"
  if (!state.hasKey) return "Add your Premium key to start"
  if (busyLabel !== "") return busyLabel
  if (state.connected && state.foreign) return "Connected by another app"
  if (state.connected) {
    var up = uptime(state, nowMs)
    return placeName(country(state, state.country), state.country) + " · " + protocolLabel(state.protocol)
      + (up !== "" ? " · " + up : "")
  }
  if (state.picked.country !== "") {
    return "Off · next: " + placeName(country(state, state.picked.country), state.picked.country)
      + " · " + protocolLabel(state.picked.protocol)
  }
  return state.account.known ? "Off · pick a location below" : "Off"
}

function barTooltip(state, busyLabel, lastError, nowMs) {
  if (lastError !== "") return "Amnezia: " + lastError
  if (!state.ok) return "Amnezia"
  if (!state.hasKey) return "Amnezia: add your Premium key"
  if (busyLabel !== "") return "Amnezia: " + busyLabel
  if (state.connected && state.foreign) return "Amnezia: connected by another app"
  if (state.connected) {
    var up = uptime(state, nowMs)
    return "Amnezia: " + placeName(country(state, state.country), state.country) + " · " + protocolLabel(state.protocol)
      + "\n↓ " + formatBytes(state.rx) + "   ↑ " + formatBytes(state.tx) + (up !== "" ? "   " + up : "")
  }
  var tip = "Amnezia: off"
  if (state.picked.country !== "")
    tip += " — next: " + placeName(country(state, state.picked.country), state.picked.country)
      + " · " + protocolLabel(state.picked.protocol)
  if (state.daysLeft !== null && state.daysLeft < 7) tip += "\nSubscription ends in " + Math.max(0, state.daysLeft) + " days"
  return tip
}

function validUntil(state) {
  if (state.account.endDate === "") return ""
  if (state.daysLeft === null) return state.account.endDate
  if (state.daysLeft < 0) return state.account.endDate + " · expired"
  return state.account.endDate + " · " + state.daysLeft + (state.daysLeft === 1 ? " day" : " days")
}

// pkexec exits 126 when the polkit dialog is dismissed and 127 when it cannot
// run at all; both read as gibberish next to a switch.
function commandError(exitCode, stderr, stdout) {
  var text = String(stderr || "").trim()
  if (text === "") text = String(stdout || "").trim()
  var lines = text.split("\n")
  text = lines[lines.length - 1]
  if (exitCode === 126 || /dismissed|not authorized|authorization cancelled/i.test(text)) return "Authorization cancelled"
  text = text.replace(/^amnezia:\s*/, "").replace(/\s+/g, " ").trim()
  if (text === "") return "Failed (exit " + exitCode + ")"
  return text.length > 200 ? text.substring(0, 197) + "…" : text
}
