import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// State for the Amnezia panel. Everything that touches the network, the key or
// the tunnel lives in scripts/amnezia; this runs it, keeps the last answer and
// exposes it as properties the panel binds to.
//
// Separate processes on purpose: `stateProcess` reads local files only and is
// safe to fire at any time, `accountProcess` asks Amnezia, and
// `controlProcess` runs the one action at a time that changes something. A
// slow polkit dialog or a slow gateway never holds up a poll.
Item {
  id: root

  required property string cliPath
  property var settings: ({})
  property bool panelOpen: false

  property var state: Model.emptyState()
  // Held apart from `state` and replaced only when they change: the poll
  // returns fresh counters every few seconds, and a new array each time would
  // rebuild every row, losing the hover and the keyboard cursor.
  property var countries: []
  property var devices: []
  property var recent: []
  property string countriesRaw: ""
  property string devicesRaw: ""
  property string recentRaw: ""

  property string lastError: ""
  property string actionStatus: ""
  property string notice: ""
  property bool accountRefreshing: false

  // Optimism, so the switch throws on click rather than when the tunnel
  // settles: -1 follows reality, 0/1 override it until reality catches up.
  property int desired: -1
  property string pendingCountry: ""
  property string pendingProtocol: ""

  readonly property bool ready: state.ok
  readonly property bool hasKey: state.hasKey
  readonly property bool connected: desired === -1 ? state.connected : desired === 1
  readonly property bool working: controlProcess.running
  readonly property bool busy: working || state.busy
  readonly property string liveCountry: state.connected && !state.foreign ? state.country : ""
  readonly property string protocol: pendingProtocol !== ""
    ? pendingProtocol : (state.connected && !state.foreign ? state.protocol : state.picked.protocol)
  readonly property bool notificationsOn: String(setting("notifications", "On")) !== "Off"

  // Traffic, sampled from the tunnel's own kernel counters once a second, and
  // only while the panel is open: it costs nothing when nobody is looking.
  readonly property int trafficSamples: 60
  property var rxHistory: []
  property var txHistory: []
  property real rxRate: 0
  property real txRate: 0
  property real rxTotal: 0
  property real txTotal: 0
  property real lastRx: -1
  property real lastTx: -1
  property string sampledIface: ""

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function refresh() {
    if (stateProcess.running || cliPath === "") return
    stateProcess.command = ["python3", cliPath, "state"]
    stateProcess.running = true
  }

  function refreshAccount() {
    if (accountProcess.running || cliPath === "" || !hasKey) return
    accountRefreshing = true
    accountProcess.command = ["python3", cliPath, "refresh"]
    accountProcess.running = true
  }

  function applyState(raw) {
    var parsed = Model.parseState(raw)
    if (!parsed) return
    var countriesText = JSON.stringify(parsed.countries)
    if (countriesText !== countriesRaw) { countriesRaw = countriesText; countries = parsed.countries }
    var devicesText = JSON.stringify(parsed.devices)
    if (devicesText !== devicesRaw) { devicesRaw = devicesText; devices = parsed.devices }
    var recentText = JSON.stringify(parsed.recent)
    if (recentText !== recentRaw) { recentRaw = recentText; recent = parsed.recent }
    state = parsed
    if (desired !== -1 && parsed.connected === (desired === 1) && !controlProcess.running) desired = -1
    if (!controlProcess.running) { pendingCountry = ""; pendingProtocol = "" }
    if (parsed.iface !== sampledIface) resetTraffic()
  }

  // ---------------------------------------------------------------- actions

  // `env` carries what must not appear on a command line (the key).
  function run(args, label, desiredState, env) {
    if (controlProcess.running || cliPath === "") return false
    desired = desiredState === undefined ? -1 : desiredState
    actionStatus = label || ""
    lastError = ""
    notice = ""
    var environment = { "OMARCHY_AMNEZIA_NO_POKE": "1" }
    if (env) for (var name in env) environment[name] = env[name]
    controlProcess.environment = environment
    controlProcess.command = ["python3", cliPath].concat(args)
    controlProcess.running = true
    return true
  }

  function placeName(code) {
    return Model.placeName(Model.country(state, code), code)
  }

  function connectTo(code, protocol) {
    var entry = Model.country(state, code)
    var wanted = Model.protocolFor(entry, Model.protocolOf(protocol || root.protocol))
    if (liveCountry === code && state.protocol === wanted && state.connected) {
      disconnect()
      return
    }
    if (run(["up", code, wanted], (connected ? "Switching to " : "Connecting to ") + placeName(code) + "…", 1)) {
      pendingCountry = code
      pendingProtocol = wanted
    }
  }

  function disconnect() {
    run(["down"], "Disconnecting…", 0)
  }

  function toggle() {
    if (!hasKey) return
    if (connected) disconnect()
    else {
      var code = state.picked.country
      if (run(["up"], code !== "" ? "Connecting to " + placeName(code) + "…" : "Connecting…", 1)) pendingCountry = code
    }
  }

  // Picking a protocol is only picking while nothing is up; over a live
  // tunnel it moves the tunnel, the way the app does.
  function setProtocol(protocol) {
    var wanted = Model.protocolOf(protocol)
    if (wanted === root.protocol) return
    var code = liveCountry !== "" ? liveCountry : state.picked.country
    var entry = Model.country(state, code)
    if (entry && !Model.offers(entry, wanted)) {
      lastError = entry.name + " has no " + Model.protocolLabel(wanted) + " — pick another location"
      errorTimer.restart()
      return
    }
    if (liveCountry !== "") {
      if (run(["up", liveCountry, wanted], "Switching to " + Model.protocolLabel(wanted) + "…", 1)) {
        pendingCountry = liveCountry
        pendingProtocol = wanted
      }
    } else if (run(["protocol", wanted], "", desired)) {
      pendingProtocol = wanted
    }
  }

  function renewConfig() {
    run(["renew"], "Asking Amnezia for a new config…", desired)
  }

  function revokeDevice(uuid) {
    run(["revoke", uuid], "Freeing the slot…", desired)
  }

  function saveKey(text) {
    var key = String(text || "").trim()
    if (key === "") return false
    return run(["key", "--env"], "Checking the key with Amnezia…", desired, { "AMNEZIA_KEY": key })
  }

  function forgetKey() {
    run(["forget"], "Giving the slot back and forgetting the key…", 0)
  }

  function openRenewal() {
    if (renewalProcess.running || cliPath === "") return
    actionStatus = "Asking Amnezia for the renewal link…"
    lastError = ""
    renewalProcess.command = ["python3", cliPath, "renewal"]
    renewalProcess.running = true
  }

  function notify(text) {
    if (!notificationsOn || panelOpen) return
    Quickshell.execDetached(["notify-send", "-a", "Amnezia", "Amnezia", text])
  }

  // ---------------------------------------------------------------- traffic

  function resetTraffic() {
    rxHistory = []
    txHistory = []
    rxRate = 0
    txRate = 0
    lastRx = -1
    lastTx = -1
    sampledIface = state.iface
  }

  function sampleTraffic() {
    if (trafficProcess.running || state.iface === "") return
    var base = "/sys/class/net/" + state.iface + "/statistics/"
    // sysfs reports a size of 0, which a FileView reads as empty; cat reads
    // to EOF and costs a millisecond.
    trafficProcess.command = ["cat", base + "rx_bytes", base + "tx_bytes"]
    trafficProcess.running = true
  }

  function applyTraffic(text) {
    var parts = String(text || "").trim().split(/\s+/)
    var rx = parseFloat(parts[0])
    var tx = parseFloat(parts[1])
    if (!isFinite(rx) || !isFinite(tx)) return
    rxTotal = rx
    txTotal = tx
    if (lastRx >= 0 && rx >= lastRx && tx >= lastTx) {
      rxRate = rx - lastRx
      txRate = tx - lastTx
      var rxs = rxHistory.slice(Math.max(0, rxHistory.length - trafficSamples + 1))
      var txs = txHistory.slice(Math.max(0, txHistory.length - trafficSamples + 1))
      rxs.push(rxRate)
      txs.push(txRate)
      rxHistory = rxs
      txHistory = txs
    }
    lastRx = rx
    lastTx = tx
  }

  // ---------------------------------------------------------------- timers

  Timer {
    interval: Math.max(2, Math.min(600, parseInt(String(root.setting("refreshIntervalSec", 5)), 10) || 5)) * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // A tunnel takes a few seconds to settle after an action: look again sooner
  // than the poll would.
  Timer {
    id: settleTimer
    property int ticks: 0
    interval: 900
    repeat: true
    onTriggered: {
      root.refresh()
      if (++ticks >= 5) {
        ticks = 0
        running = false
        root.desired = -1
      }
    }
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.panelOpen && root.state.connected && root.state.iface !== ""
    onTriggered: root.sampleTraffic()
    onRunningChanged: if (running) root.sampleTraffic()
  }

  Timer {
    id: errorTimer
    interval: 9000
    onTriggered: root.lastError = ""
  }

  Timer {
    id: noticeTimer
    interval: 4000
    onTriggered: root.notice = ""
  }

  // ------------------------------------------------------------- processes

  Process {
    id: stateProcess
    running: false
    stdout: StdioCollector { id: stateOut; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyState(stateOut.text)
    }
  }

  Process {
    id: accountProcess
    running: false
    stdout: StdioCollector { id: accountOut; waitForEnd: true }
    stderr: StdioCollector { id: accountErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.accountRefreshing = false
      if (exitCode === 0) root.applyState(accountOut.text)
      else {
        root.lastError = Model.commandError(exitCode, accountErr.text, "")
        errorTimer.restart()
      }
    }
  }

  Process {
    id: controlProcess
    running: false
    stdout: StdioCollector { id: controlOut; waitForEnd: true }
    stderr: StdioCollector { id: controlErr; waitForEnd: true }
    onExited: function(exitCode) {
      // Drop the key (if one was passed) as soon as the process is done.
      controlProcess.environment = ({ "OMARCHY_AMNEZIA_NO_POKE": "1" })
      root.actionStatus = ""
      if (exitCode === 0) {
        var lines = String(controlOut.text || "").trim().split("\n")
        root.notice = Model.clip(lines[lines.length - 1], 200)
        noticeTimer.restart()
        root.notify(root.notice)
      } else {
        root.desired = -1
        root.lastError = Model.commandError(exitCode, controlErr.text, controlOut.text)
        errorTimer.restart()
        root.notify(root.lastError)
      }
      root.pendingCountry = ""
      root.pendingProtocol = ""
      settleTimer.ticks = 0
      settleTimer.restart()
      root.refresh()
    }
  }

  Process {
    id: renewalProcess
    running: false
    stdout: StdioCollector { id: renewalOut; waitForEnd: true }
    stderr: StdioCollector { id: renewalErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.actionStatus = ""
      var url = String(renewalOut.text || "").trim()
      if (exitCode === 0 && url.indexOf("https://") === 0) {
        Qt.openUrlExternally(url)
      } else {
        root.lastError = Model.commandError(exitCode, renewalErr.text, renewalOut.text)
        errorTimer.restart()
      }
    }
  }

  Process {
    id: trafficProcess
    running: false
    stdout: StdioCollector { id: trafficOut; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyTraffic(trafficOut.text)
    }
  }
}
