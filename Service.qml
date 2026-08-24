import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// What the panel and the IPC surface both talk to.
//
// Two ways to raise a tunnel, picked per refresh: hand the config to an
// already-installed AmneziaVPN service, or run awg-quick through pkexec
// ourselves. Everything else — which config is live, what it is moving, what
// went wrong — is derived from the store and the service in one place so the
// panel can bind to it and the IPC methods can read it.
Item {
  id: root

  property var settings: ({})

  property string lastError: ""
  property string notice: ""
  property real since: 0

  // Optimistic connection state: the switch has to throw when it is clicked,
  // not when awg-quick returns. -1 follows reality, 0/1 override it until
  // reality catches up.
  property int desired: -1

  readonly property var configs: store.configs
  readonly property var tools: store.tools
  readonly property string selected: store.selected
  readonly property bool hasConfigs: store.hasConfigs
  readonly property bool ready: store.ready

  readonly property string backendSetting: String(setting("backend", "auto"))
  readonly property string backend: backendSetting === "daemon" ? "daemon"
    : (backendSetting === "quick" ? "quick" : (daemon.available ? "daemon" : "quick"))
  readonly property bool daemonBackend: backend === "daemon"

  readonly property bool busy: daemon.busy || quickProcess.running || confProcess.running
    || resolveProcess.running
  readonly property bool reallyConnected: daemonBackend ? daemon.tunnelUp : liveConfigName() !== ""
  readonly property bool connected: desired === -1 ? reallyConnected : (desired === 1)
  readonly property string activeName: activeConfigName()
  readonly property bool activeForeign: connected && activeName === ""
  readonly property var activeConfig: store.configFor(activeName)
  readonly property real rxBytes: daemonBackend ? daemon.rxBytes
    : (activeConfig ? activeConfig.rxBytes : 0)
  readonly property real txBytes: daemonBackend ? daemon.txBytes
    : (activeConfig ? activeConfig.txBytes : 0)
  readonly property bool toolsMissing: !daemonBackend && !tools.awgQuick && !tools.wgQuick

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 15, 2, 600)
  readonly property bool switchWhenConnected: String(setting("switchWhenConnected", "On")) !== "Off"

  signal changed()

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var parsed = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(parsed)) parsed = fallback
    return Math.max(min, Math.min(max, parsed))
  }

  // The config whose own interface is up. Only the awg-quick backend can
  // answer this from the kernel; the service always names its interface amn0.
  function liveConfigName() {
    for (var i = 0; i < store.configs.length; i++) {
      if (store.configs[i].up === true) return store.configs[i].name
    }
    return ""
  }

  // Which config is running. On the service backend that is what we last told
  // it to run, but only while the address it reports still matches — otherwise
  // the app moved the tunnel and our record is stale.
  function activeConfigName() {
    if (!daemonBackend) return liveConfigName()
    if (!daemon.tunnelUp) return ""

    var device = Model.stripMask(daemon.deviceAddress)
    var remembered = store.configFor(store.active)
    if (remembered && (device === "" || Model.stripMask(remembered.address) === device)) {
      return remembered.name
    }
    if (device === "") return ""
    for (var i = 0; i < store.configs.length; i++) {
      if (Model.stripMask(store.configs[i].address) === device) return store.configs[i].name
    }
    return ""
  }

  function configFor(name) {
    return store.configFor(name)
  }

  function refresh() {
    store.refresh()
    daemon.poll()
  }

  function report(message) {
    lastError = String(message || "")
    if (lastError !== "") notify("Amnezia", lastError)
    desired = -1
    settleTimer.restart()
  }

  function announce(message) {
    notice = String(message || "")
    lastError = ""
    noticeTimer.restart()
  }

  // The IPC methods answer before the work finishes, so a terminal caller
  // learns the outcome the same way the desktop does.
  function notify(title, body) {
    Quickshell.execDetached(["notify-send", "-a", "Amnezia", String(title), String(body)])
  }

  // --------------------------------------------------------------- actions

  function select(name) {
    var target = String(name || "")
    if (target === "" || target === store.selected) return
    if (!store.exists(target)) {
      report("no such config: " + target)
      return
    }
    store.select(target)
    if (connected && switchWhenConnected) up(target)
  }

  function up(name) {
    var target = String(name || store.selected || "")
    if (target === "") {
      report(store.hasConfigs ? "no config selected" : "no configs yet — import one first")
      return
    }
    if (!store.exists(target)) {
      report("no such config: " + target)
      return
    }
    if (busy) return

    pendingName = target
    store.select(target)
    desired = 1

    if (!daemonBackend) {
      var missing = Model.missingToolFor(store.configFor(target), backend, tools)
      if (missing !== "") {
        report("that config " + missing)
        return
      }
      runQuick("up", target)
      return
    }

    // The service takes the whole config as one message, so it has to be read
    // and its endpoint resolved before anything is sent.
    confProcess.command = ["cat", "--", store.pathFor(target)]
    confProcess.running = true
  }

  function down() {
    if (busy) return
    desired = 0
    if (daemonBackend) {
      if (!daemon.tunnelUp) { desired = -1; return }
      store.setActive("")
      daemon.deactivate()
      return
    }
    var live = liveConfigName()
    if (live === "") { desired = -1; return }
    runQuick("down", live)
  }

  function toggle() {
    if (connected) down()
    else up("")
  }

  function importConfig(path, name) {
    store.importConfig(path, name)
  }

  function removeConfig(name) {
    if (activeName === String(name)) down()
    store.removeConfig(name)
  }

  // ----------------------------------------------------- the awg-quick path

  function quickTool(name) {
    var config = store.configFor(name)
    var protocol = config ? String(config.protocol) : "awg"
    if (protocol === "awg") return tools.awgQuick ? "awg-quick" : ""
    if (tools.wgQuick) return "wg-quick"
    return tools.awgQuick ? "awg-quick" : ""
  }

  function runQuick(action, name) {
    var tool = quickTool(name)
    if (tool === "") {
      var missing = Model.missingToolFor(store.configFor(name), backend, tools)
      report(missing !== "" ? "that config " + missing : "no awg-quick or wg-quick installed")
      return
    }
    // One tunnel at a time: two default routes would be a coin toss.
    var live = liveConfigName()
    quickProcess.action = action
    quickProcess.target = name
    if (action === "up" && live !== "" && live !== name) {
      quickProcess.thenUp = name
      quickProcess.command = ["pkexec", quickTool(live), "down", store.pathFor(live)]
    } else {
      quickProcess.thenUp = ""
      quickProcess.command = ["pkexec", tool, action, store.pathFor(name)]
    }
    quickProcess.running = true
  }

  // ------------------------------------------------- the service's payload

  property string pendingName: ""
  property string pendingConf: ""

  function confRead(text, exitCode, stderr) {
    if (exitCode !== 0) {
      report(Model.commandError(exitCode, stderr, "could not read the config"))
      return
    }
    pendingConf = text
    var host = Model.endpointHost(Model.parseConf(text).peer.endpoint)
    if (Model.isIpv4(host)) {
      activateWith(host)
      return
    }
    // The service turns this string into a route and a tunnel endpoint, and
    // resolves neither, so a name has to become an address first.
    resolveProcess.command = ["getent", "ahostsv4", host]
    resolveProcess.running = true
  }

  function resolved(text, exitCode) {
    var address = ""
    var lines = String(text || "").split("\n")
    for (var i = 0; i < lines.length && address === ""; i++) {
      var first = lines[i].trim().split(/\s+/)[0]
      if (Model.isIpv4(first)) address = first
    }
    if (exitCode !== 0 || address === "") {
      report("could not resolve " + Model.endpointHost(Model.parseConf(pendingConf).peer.endpoint))
      pendingConf = ""
      return
    }
    activateWith(address)
  }

  function activateWith(address) {
    var built = Model.activatePayload(pendingConf, address)
    pendingConf = ""
    if (built.error) {
      report(built.error)
      return
    }
    // Recorded before the answer comes back: the address cross-check in
    // activeConfigName() corrects it if the service ends up running something
    // else, and a record for a tunnel that never came up is ignored anyway.
    store.setActive(pendingName)
    daemon.activate(built.payload)
  }

  // ------------------------------------------------------------ text for IPC

  function statusText() {
    var via = Model.backendLabel(backend)
    if (!connected) {
      if (!store.hasConfigs) return "disconnected — no configs yet"
      return "disconnected (selected: " + store.selected + ") via " + via
    }
    if (activeName === "") return "connected to a config this plugin does not have, via " + via
    return "connected: " + activeName + " via " + via
  }

  function listText() {
    if (!store.hasConfigs) return "no configs yet"
    var lines = []
    for (var i = 0; i < store.configs.length; i++) {
      var config = store.configs[i]
      lines.push((config.name === activeName ? "* " : "  ") + config.name
        + "  " + Model.protocolLabel(config.protocol)
        + "  " + config.endpoint
        + (config.name === store.selected ? "  (selected)" : ""))
    }
    return lines.join("\n")
  }

  // --------------------------------------------------------------- plumbing

  Store {
    id: store
    onChanged: {
      if (root.desired !== -1 && root.reallyConnected === (root.desired === 1)) root.desired = -1
      root.changed()
    }
    onFailed: function(reason) { root.report(reason) }
    onImported: function(name) {
      root.announce("Imported " + name)
      root.notify("Amnezia", "Imported " + name)
    }
  }

  Daemon {
    id: daemon
    socketPath: store.socketPath
    onChanged: {
      if (root.desired !== -1 && daemon.tunnelUp === (root.desired === 1)) root.desired = -1
      if (daemon.tunnelUp && root.since === 0) root.since = Date.now()
      if (!daemon.tunnelUp) root.since = 0
      root.changed()
    }
    onFailed: function(reason) { root.report(reason) }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    // awg-quick returns before the interface has settled, and the service's
    // counters only move after the handshake, so poll a few times after every
    // action instead of waiting out the interval.
    id: settleTimer
    property int ticks: 0
    interval: 900
    repeat: true
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
    id: noticeTimer
    interval: 2600
    repeat: false
    onTriggered: root.notice = ""
  }

  Process {
    id: quickProcess
    property string action: ""
    property string target: ""
    property string thenUp: ""
    running: false
    command: []
    stderr: StdioCollector { id: quickErr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.report(Model.commandError(exitCode, quickErr.text, ""))
        return
      }
      if (thenUp !== "") {
        // The old tunnel is down; now raise the one that was asked for.
        var next = thenUp
        thenUp = ""
        root.runQuick("up", next)
        return
      }
      root.lastError = ""
      root.since = action === "up" ? Date.now() : 0
      settleTimer.restart()
    }
  }

  Process {
    id: confProcess
    running: false
    command: []
    stdout: StdioCollector { id: confOut; waitForEnd: true }
    stderr: StdioCollector { id: confErr; waitForEnd: true }
    onExited: function(exitCode) { root.confRead(confOut.text, exitCode, confErr.text) }
  }

  Process {
    id: resolveProcess
    running: false
    command: []
    stdout: StdioCollector { id: resolveOut; waitForEnd: true }
    onExited: function(exitCode) { root.resolved(resolveOut.text, exitCode) }
  }
}
