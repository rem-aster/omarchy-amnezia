// Pure formatting and parsing for the Amnezia panel. Everything here takes
// the JSON `omarchy-amnezia status --json` prints and turns it into the
// strings the panel shows, so the QML stays about layout and state.

// The script bounds its own answer, but the panel is the thing that would
// suffer, so it does not take the script's word for it. Anything past these
// ceilings is refused rather than parsed and laid out.
var maxStatusBytes = 131072
var maxProfileRows = 64
var maxFieldChars = 256

function clampText(value) {
  var text = String(value === undefined || value === null ? "" : value)
  return text.length > maxFieldChars ? text.substring(0, maxFieldChars) : text
}

function clampCount(value) {
  var count = Number(value || 0)
  return isFinite(count) && count > 0 ? Math.floor(count) : 0
}

function defaultStatus() {
  return {
    ok: false,
    tooLarge: false,
    profileTotal: 0,
    profileLimit: 0,
    selected: "",
    active: false,
    activeProfile: "",
    protocol: "",
    rxBytes: 0,
    txBytes: 0,
    since: 0,
    activeForeign: false,
    profiles: [],
    tools: { backend: "", daemon: false, awgQuick: false, wgQuick: false,
             resolvconf: false, priv: "none" }
  }
}

function parseStatus(raw) {
  var text = String(raw || "").trim()
  if (text === "") return defaultStatus()
  if (text.length > maxStatusBytes) {
    var oversized = defaultStatus()
    oversized.tooLarge = true
    return oversized
  }

  var parsed
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    return defaultStatus()
  }
  if (!parsed || typeof parsed !== "object") return defaultStatus()
  // The script refuses to print an answer past its own ceiling and says so
  // instead; treat that the same as an answer too large to read.
  if (typeof parsed.error === "string" && parsed.error !== "") {
    var refused = defaultStatus()
    refused.tooLarge = true
    return refused
  }

  var status = defaultStatus()
  status.ok = true
  status.selected = String(parsed.selected || "")
  status.active = parsed.active === true
  // The service can be running a config this plugin never imported — the
  // official app connecting to another server looks exactly like that.
  status.activeForeign = parsed.activeForeign === true
  status.activeProfile = String(parsed.activeProfile || "")
  status.protocol = String(parsed.protocol || "")
  status.rxBytes = Number(parsed.rxBytes || 0)
  status.txBytes = Number(parsed.txBytes || 0)
  status.since = Number(parsed.since || 0)
  status.profileTotal = clampCount(parsed.profileTotal)
  status.profileLimit = clampCount(parsed.profileLimit)
  // Every string that reaches a Text element is clamped here, so one long line
  // in a .conf cannot become one long line in the bar.
  status.profiles = []
  var rows = Array.isArray(parsed.profiles) ? parsed.profiles.slice(0, maxProfileRows) : []
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i]
    if (!row || typeof row !== "object") continue
    status.profiles.push({
      name: clampText(row.name),
      protocol: clampText(row.protocol),
      endpoint: clampText(row.endpoint),
      address: clampText(row.address),
      dns: clampText(row.dns),
      mtu: clampText(row.mtu),
      active: row.active === true,
      selected: row.selected === true,
      rxBytes: Number(row.rxBytes || 0),
      txBytes: Number(row.txBytes || 0)
    })
  }
  if (parsed.tools && typeof parsed.tools === "object") {
    status.tools = {
      backend: String(parsed.tools.backend || ""),
      daemon: parsed.tools.daemon === true,
      awgQuick: parsed.tools.awgQuick === true,
      wgQuick: parsed.tools.wgQuick === true,
      resolvconf: parsed.tools.resolvconf === true,
      priv: String(parsed.tools.priv || "none")
    }
  }
  return status
}

// When the script capped the list, say so: configs that quietly went missing
// would be worse than a shorter list that admits it.
function configsHeading(status) {
  var shown = status.profiles.length
  var total = status.profileTotal
  return total > shown ? "CONFIGS — SHOWING " + shown + " OF " + total : "CONFIGS"
}

function protocolLabel(protocol) {
  if (String(protocol) === "awg") return "AmneziaWG"
  if (String(protocol) === "wireguard") return "WireGuard"
  return ""
}

// The tool a config needs is decided by the config, so a config the box
// cannot run should say so on its own row rather than only on failure. The
// AmneziaVPN service brings its own bundled backend, so in that mode there is
// nothing on the box to be missing.
function missingToolFor(profile, tools) {
  if (!profile || !tools) return ""
  if (String(tools.backend) === "daemon") return ""
  if (String(profile.protocol) === "awg") return tools.awgQuick ? "" : "needs amneziawg-tools"
  return tools.wgQuick || tools.awgQuick ? "" : "needs wireguard-tools"
}

// How the tunnel is being raised, for the panel's own line about it.
function backendLabel(tools) {
  if (!tools) return ""
  if (String(tools.backend) === "daemon") return "AmneziaVPN service"
  if (String(tools.backend) === "quick") return "awg-quick"
  return ""
}

function profileMeta(profile, tools) {
  if (!profile) return ""
  var parts = []
  var label = protocolLabel(profile.protocol)
  if (label !== "") parts.push(label)
  var endpoint = String(profile.endpoint || "")
  if (endpoint !== "") parts.push(endpoint)
  var missing = missingToolFor(profile, tools)
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
  var decimals = value < 10 && index > 0 ? 1 : 0
  return value.toFixed(decimals) + " " + units[index]
}

function transferText(rxBytes, txBytes) {
  return "↓ " + formatBytes(rxBytes) + "   ↑ " + formatBytes(txBytes)
}

// `since` is stamped by the CLI when it brings a tunnel up, so an uptime is
// only shown for tunnels this plugin started in this boot.
function uptimeText(sinceSec, nowMs) {
  var since = Number(sinceSec || 0)
  if (!isFinite(since) || since <= 0) return ""
  var now = nowMs === undefined ? Date.now() : Number(nowMs)
  var seconds = Math.max(0, Math.floor(now / 1000 - since))
  if (seconds < 60) return seconds + "s"
  var minutes = Math.floor(seconds / 60)
  if (minutes < 60) return minutes + "m"
  var hours = Math.floor(minutes / 60)
  if (hours < 24) return hours + "h " + (minutes % 60) + "m"
  var days = Math.floor(hours / 24)
  return days + "d " + (hours % 24) + "h"
}

function heroMeta(status, busy, profileCount, nowMs) {
  if (busy) return "Working"
  if (!status.active) {
    if (Number(profileCount) === 0) return "No configs"
    return "Disconnected"
  }
  if (status.activeForeign) return "Connected · another config"
  var uptime = uptimeText(status.since, nowMs)
  return uptime === "" ? "Connected" : "Connected · " + uptime
}

// pkexec exits 126 when the polkit dialog is dismissed and 127 when it can
// not be started at all; both read as gibberish next to a switch.
function commandError(exitCode, stderr, stdout) {
  var text = String(stderr || "").trim()
  if (text === "") text = String(stdout || "").trim()
  if (exitCode === 126 || /dismissed|not authorized|Authentication failed/i.test(text)) {
    return "Authorization cancelled"
  }
  if (exitCode === 127 && text === "") return "pkexec is missing"
  text = text.replace(/^omarchy-amnezia:\s*/, "").replace(/\s+/g, " ").trim()
  if (text === "") return "Command failed (exit " + exitCode + ")"
  return text.length > 160 ? text.substring(0, 157) + "…" : text
}
