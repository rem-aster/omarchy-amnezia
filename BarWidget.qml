import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Amnezia Premium in the Omarchy bar. The widget is only a status light and
// two shortcuts: left click opens the dashboard (scripts/amnezia, a TUI in a
// floating terminal, the way omarchy-vpn does it), right click connects to the
// picked location or disconnects. Everything that touches the network, the
// key or the tunnel lives in the script; this polls `status --json` and shows
// the answer.
BarWidget {
  id: root
  moduleName: "rem-aster.amnezia"

  // The plugin's own scripts, run from wherever this file was loaded, so a
  // checkout placed by hand keeps working; the install path is the fallback.
  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") === 0) url = url.substring(7)
    url = decodeURIComponent(url).replace(/\/+$/, "")
    if (url === "") url = Quickshell.env("HOME") + "/.config/omarchy/plugins/rem-aster.amnezia"
    return url
  }
  readonly property string cliPath: pluginDir + "/scripts/amnezia"

  readonly property int refreshIntervalSec: {
    var value = parseInt(String(setting("refreshIntervalSec", 5)), 10)
    return isFinite(value) ? Math.max(2, Math.min(600, value)) : 5
  }
  readonly property bool showLocation: String(setting("showLocation", "On")) !== "Off"
  readonly property bool rightClickToggles: String(setting("rightClick", "Toggle")) !== "Nothing"

  // The last answer of `status --json`; `ok` stays false until one arrives.
  property var info: ({ ok: false })
  property bool acting: false
  property string lastError: ""

  readonly property bool connected: info.connected === true
  readonly property bool busy: acting || info.busy === true
  readonly property string countryCode: String(info.country || "").split("-")[0].toUpperCase()

  readonly property string shieldOn: String.fromCodePoint(0xF0565)
  readonly property string shieldOff: String.fromCodePoint(0xF0498)
  readonly property string loadingGlyph: "󰦖"

  function clip(value, limit) {
    var text = String(value === undefined || value === null ? "" : value)
    return text.length > limit ? text.substring(0, limit) + "…" : text
  }

  function formatBytes(bytes) {
    var value = Number(bytes || 0)
    if (!isFinite(value) || value <= 0) return "0 B"
    var units = ["B", "KB", "MB", "GB", "TB"]
    var index = 0
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index++ }
    return value.toFixed(value < 10 && index > 0 ? 1 : 0) + " " + units[index]
  }

  function formatSince(since) {
    var start = Number(since || 0)
    if (!isFinite(start) || start <= 0) return ""
    var minutes = Math.max(0, Math.floor((Date.now() / 1000 - start) / 60))
    if (minutes < 60) return minutes + "m"
    var hours = Math.floor(minutes / 60)
    if (hours < 24) return hours + "h " + (minutes % 60) + "m"
    return Math.floor(hours / 24) + "d " + (hours % 24) + "h"
  }

  readonly property string tooltip: {
    if (root.lastError !== "") return "Amnezia: " + root.lastError
    if (!info.ok) return "Amnezia"
    if (info.hasKey !== true) return "Amnezia: add your Premium key (click)"
    var place = clip(String(info.flag || "") + " " + String(info.countryName || ""), 60).trim()
    var protocol = clip(info.protocolLabel, 20)
    if (root.busy) return "Amnezia: working…"
    if (root.connected && info.foreign === true) return "Amnezia: connected by another app"
    if (root.connected) {
      var lines = ["Amnezia: " + place + " · " + protocol]
      var since = formatSince(info.since)
      lines.push("↓ " + formatBytes(info.rx) + "   ↑ " + formatBytes(info.tx) + (since !== "" ? "   " + since : ""))
      return lines.join("\n")
    }
    var off = "Amnezia: off" + (place !== "" ? " — next: " + place + " · " + protocol : "")
    if (info.daysLeft !== null && info.daysLeft !== undefined && Number(info.daysLeft) < 7)
      off += "\nSubscription ends in " + Math.max(0, Number(info.daysLeft)) + " days"
    return off
  }

  function refresh() {
    if (statusProcess.running) return
    statusProcess.command = ["python3", root.cliPath, "status", "--json"]
    statusProcess.running = true
  }

  function openDashboard() {
    Quickshell.execDetached(["python3", root.cliPath, "open"])
  }

  function toggleConnection() {
    if (controlProcess.running) return
    if (info.ok && info.hasKey !== true) { openDashboard(); return }
    root.acting = true
    root.lastError = ""
    controlProcess.command = ["python3", root.cliPath, info.connected === true ? "down" : "up"]
    controlProcess.running = true
  }

  function applyStatus(raw) {
    var text = String(raw || "").trim()
    if (text === "" || text.length > 65536) return
    try {
      var parsed = JSON.parse(text)
      if (parsed && typeof parsed === "object" && parsed.ok === true) root.info = parsed
    } catch (error) {}
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  ShellIpc {
    target: "amnezia"

    function open(): void { root.openDashboard() }
    function toggle(): void { root.toggleConnection() }
    function refresh(): void { root.broadcast("refresh") }
    function status(): string {
      if (!root.info.ok) return "unknown"
      return root.connected
        ? "connected: " + root.info.countryName + " · " + root.info.protocolLabel
        : "disconnected"
    }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // A switch takes a few seconds to settle; look again sooner than the timer.
  Timer {
    id: settleTimer
    property int ticks: 0
    interval: 1000
    repeat: true
    onTriggered: {
      root.refresh()
      if (++ticks >= 5) { ticks = 0; running = false }
    }
  }

  Timer {
    id: errorTimer
    interval: 6000
    onTriggered: root.lastError = ""
  }

  Process {
    id: statusProcess
    running: false
    stdout: StdioCollector { id: statusOut; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyStatus(statusOut.text)
    }
  }

  Process {
    id: controlProcess
    running: false
    environment: ({ "OMARCHY_AMNEZIA_NO_POKE": "1" })
    stdout: StdioCollector { id: controlOut; waitForEnd: true }
    stderr: StdioCollector { id: controlErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.acting = false
      if (exitCode !== 0) {
        var message = String(controlErr.text || controlOut.text || "").trim().replace(/^amnezia:\s*/, "")
        root.lastError = root.clip(message.split("\n").pop() || ("failed (exit " + exitCode + ")"), 160)
        errorTimer.restart()
        Quickshell.execDetached(["notify-send", "-a", "Amnezia", "Amnezia", root.lastError])
      }
      settleTimer.ticks = 0
      settleTimer.restart()
      root.refresh()
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.busy ? root.loadingGlyph : (root.connected ? root.shieldOn : root.shieldOff)
    labelText: root.showLocation && root.connected && !root.vertical ? root.countryCode : ""
    dimmed: !root.connected && !root.busy
    tooltipText: root.tooltip
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) {
        if (root.rightClickToggles) root.toggleConnection()
        else root.openDashboard()
      } else if (buttonCode === Qt.MiddleButton) {
        root.refresh()
      } else {
        root.openDashboard()
      }
    }
  }
}
