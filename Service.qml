import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// State for the Amnezia widget. Everything that touches disk or the network
// stack lives in scripts/amnezia; this runs it, keeps the last answer,
// and exposes it as properties the panel can bind to.
//
// Two processes, deliberately: `statusProcess` polls and is safe to fire at
// any time, `controlProcess` runs the one command that needs authorization.
// Keeping them apart means a slow polkit dialog never blocks a refresh, and a
// refresh never lands in the middle of a connect.
Item {
  id: root

  property var settings: ({})
  required property string cliPath

  property var status: Model.defaultStatus()
  property bool refreshing: false
  property string lastError: ""
  property string actionStatus: ""

  // Optimistic connection state, same trick as the Tailscale service: the
  // switch has to throw the moment it is clicked, not when awg-quick returns.
  // -1 follows reality, 0/1 override it until reality catches up.
  property int desired: -1

  // Held separately from `status` and only replaced when it actually changes:
  // the poll returns fresh transfer counters every few seconds, and handing
  // the Repeater a new array each time would rebuild every row (losing the
  // hover and the keyboard cursor) for no new information.
  property var profiles: []
  property string profilesRaw: ""

  // Same optimism as `desired`, for the pick: the highlight moves on click
  // instead of on the next poll. Cleared once the CLI reports it back.
  property string pendingSelection: ""

  readonly property bool connected: desired === -1 ? status.active : (desired === 1)
  readonly property var tools: status.tools
  readonly property string selected: pendingSelection !== "" ? pendingSelection : status.selected
  readonly property string activeProfile: status.activeProfile
  readonly property bool busy: controlProcess.running
  readonly property bool hasProfiles: profiles.length > 0
  // False until the first poll answers, so the panel does not flash an empty
  // state or a missing-tools warning built out of the placeholder status.
  readonly property bool ready: status.ok === true
  readonly property string configsHeading: Model.configsHeading(status)
  readonly property string backend: status.tools.backend
  // Nothing to install when the AmneziaVPN service is doing the work: it
  // ships its own tunnel binary.
  readonly property bool toolsMissing: backend !== "daemon"
    && !status.tools.awgQuick && !status.tools.wgQuick

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 15, 2, 600)
  readonly property bool switchWhenConnected: String(setting("switchWhenConnected", "On")) !== "Off"

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var parsed = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(parsed)) parsed = fallback
    return Math.max(min, Math.min(max, parsed))
  }

  function profileByName(name) {
    for (var i = 0; i < profiles.length; i++) {
      if (String(profiles[i].name) === String(name)) return profiles[i]
    }
    return null
  }

  function refresh() {
    if (statusProcess.running || cliPath === "") return
    refreshing = true
    statusProcess.command = ["bash", cliPath, "status", "--json"]
    statusProcess.running = true
  }

  // Set when the poll itself is the thing that went wrong, so only that kind
  // of message is cleared by a poll that succeeds.
  property bool statusFailed: false

  function readFailed(message) {
    statusFailed = true
    lastError = message
  }

  function run(args, desiredState, label) {
    if (controlProcess.running || cliPath === "") return
    desired = desiredState
    actionStatus = label
    statusFailed = false
    lastError = ""
    controlProcess.command = ["bash", cliPath].concat(args)
    controlProcess.running = true
  }

  function up(name) {
    var target = String(name || status.selected || "")
    if (target === "") {
      statusFailed = false
      lastError = "No config to connect. Import one first."
      return
    }
    run(["up", target], 1, "Connecting…")
  }

  function down() {
    run(["down"], 0, "Disconnecting…")
  }

  function toggle() {
    if (connected) down()
    else up("")
  }

  // Picking a config always records the choice. Moving a live tunnel over to
  // it is a second, louder thing, so it only happens when the widget is set
  // up to do it.
  function select(name) {
    var target = String(name || "")
    if (target === "" || target === selected) return
    pendingSelection = target
    if (connected && switchWhenConnected) run(["up", target], 1, "Switching…")
    else run(["select", target], desired, "")
  }

  // Plain text for the IPC callers, who get a terminal line rather than a
  // panel to read.
  function statusText() {
    if (!connected) {
      if (!hasProfiles) return "disconnected — no configs yet"
      return "disconnected (selected: " + selected + ")"
    }
    return activeProfile !== ""
      ? "connected: " + activeProfile
      : "connected to a config this plugin does not have"
  }

  function listText() {
    if (!hasProfiles) return "no configs yet"
    var lines = []
    for (var i = 0; i < profiles.length; i++) {
      var config = profiles[i]
      lines.push((config.active ? "* " : "  ") + config.name
        + "  " + Model.protocolLabel(config.protocol)
        + "  " + String(config.endpoint || "")
        + (config.name === selected ? "  (selected)" : ""))
    }
    return lines.join("\n")
  }

  function importConfig(path, name) {
    var args = ["import", String(path)]
    if (String(name || "") !== "") args = args.concat(["--name", String(name)])
    run(args, desired, "Importing…")
  }

  function removeConfig(name) {
    if (String(name || "") === "") return
    run(["remove", String(name)], desired, "")
  }

  function applyStatus(raw) {
    var parsed = Model.parseStatus(raw)
    if (parsed.tooLarge) {
      readFailed("The status answer was too large to read — too many configs?")
      return
    }
    if (!parsed.ok) {
      readFailed("Could not read the Amnezia state")
      return
    }
    var serialized = JSON.stringify(parsed.profiles)
    if (serialized !== profilesRaw) {
      profilesRaw = serialized
      profiles = parsed.profiles
    }
    status = parsed
    // A read that failed should stop being reported once reading works again,
    // but an error from a click of the user's own stays until it times out.
    if (statusFailed) {
      statusFailed = false
      lastError = ""
    }
    // Reality caught up with the click — stop overriding it.
    if (desired !== -1 && parsed.active === (desired === 1)) desired = -1
    if (pendingSelection !== "" && parsed.selected === pendingSelection) pendingSelection = ""
  }

  Timer {
    id: refreshTimer
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // awg-quick returns before the interface has finished settling (and the
  // transfer counters only start moving after the handshake), so poll a few
  // times after every command instead of waiting out the interval.
  Timer {
    id: settleTimer
    property int ticks: 0
    interval: 900
    repeat: true
    running: false
    onTriggered: {
      ticks += 1
      root.refresh()
      if (ticks >= 4) {
        ticks = 0
        running = false
        root.desired = -1
      }
    }
  }

  Timer {
    id: actionStatusTimer
    interval: 2600
    repeat: false
    onTriggered: root.actionStatus = ""
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector { id: statusStdout; waitForEnd: true }
    stderr: StdioCollector { id: statusStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.refreshing = false
      if (exitCode === 0) root.applyStatus(statusStdout.text)
      else root.readFailed(Model.commandError(exitCode, statusStderr.text, statusStdout.text))
    }
  }

  Process {
    id: controlProcess
    running: false
    command: []
    stdout: StdioCollector { id: controlStdout; waitForEnd: true }
    stderr: StdioCollector { id: controlStderr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.statusFailed = false
        root.lastError = ""
        root.actionStatus = ""
      } else {
        // The click did not happen after all; drop back to whatever is real.
        root.desired = -1
        root.pendingSelection = ""
        root.lastError = Model.commandError(exitCode, controlStderr.text, controlStdout.text)
        root.actionStatus = ""
        actionStatusTimer.restart()
      }
      settleTimer.ticks = 0
      settleTimer.restart()
    }
  }
}
