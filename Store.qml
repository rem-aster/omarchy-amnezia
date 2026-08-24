import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// The configs on disk, and the one thing remembered about them.
//
// A refresh is a single `bash -c` that prints every config together with the
// live interface state and what the machine has installed, which Model.js
// turns into the list the panel binds to. One process per refresh regardless
// of how many configs there are, and no helper script to ship.
Item {
  id: root

  readonly property string home: Quickshell.env("HOME")
  readonly property string baseDir: home + "/.config/omarchy/amnezia"
  readonly property string configsDir: baseDir + "/configs"
  readonly property string statePath: baseDir + "/state.json"

  property var configs: []
  property var tools: ({ awgQuick: false, wgQuick: false, pkexec: false })
  property string socketPath: ""
  property bool ready: false
  property bool scanning: false
  property string lastError: ""

  // Persisted in state.json: what the switch acts on, and what this plugin
  // last told the service to run.
  property string selected: ""
  property string active: ""

  signal changed()
  signal imported(string name)
  signal failed(string reason)

  readonly property bool hasConfigs: configs.length > 0

  function configFor(name) {
    return Model.findConfig(configs, name)
  }

  function pathFor(name) {
    return configsDir + "/" + name + ".conf"
  }

  function exists(name) {
    return configFor(name) !== null
  }

  function refresh() {
    if (scanProcess.running) return
    scanning = true
    scanProcess.running = true
  }

  function apply(blob) {
    var parsed = Model.parseStore(blob)
    if (!parsed.ok) {
      lastError = "could not read " + configsDir
      return
    }
    lastError = ""
    configs = parsed.configs
    tools = parsed.tools
    socketPath = parsed.socket
    ready = true
    // A selection whose config is gone should not keep the switch pointed at
    // nothing; fall back to the first config there is.
    if (selected !== "" && !exists(selected)) selected = ""
    if (selected === "" && configs.length > 0) select(configs[0].name)
    changed()
  }

  function select(name) {
    var target = String(name || "")
    if (target === selected) return
    if (target !== "" && !Model.validName(target)) return
    selected = target
    saveState()
    changed()
  }

  function setActive(name) {
    active = String(name || "")
    saveState()
  }

  function saveState() {
    stateFile.setText(JSON.stringify({ selected: selected, active: active }, null, 2) + "\n")
  }

  function loadState(text) {
    try {
      var parsed = JSON.parse(String(text || "{}"))
      var storedSelected = String(parsed.selected || "")
      var storedActive = String(parsed.active || "")
      if (Model.validName(storedSelected)) selected = storedSelected
      if (Model.validName(storedActive)) active = storedActive
    } catch (error) {
      // A corrupt state file is not worth a message: the defaults are right.
    }
    refresh()
  }

  // ------------------------------------------------------------- importing

  property string pendingPath: ""
  property string pendingName: ""

  // Reads the file first and only writes it once it parses as a config, so a
  // pasted key or the wrong file never lands in the configs directory.
  function importConfig(path, name) {
    var source = String(path || "").trim()
    if (source === "") {
      failed("no file given")
      return
    }
    if (source.indexOf("~/") === 0) source = home + source.substring(1)
    if (readProcess.running || installProcess.running) {
      failed("still busy with the last import")
      return
    }
    pendingPath = source
    pendingName = String(name || "")
    readProcess.command = ["cat", "--", source]
    readProcess.running = true
  }

  function finishRead(text, exitCode, stderr) {
    if (exitCode !== 0) {
      failed(Model.commandError(exitCode, stderr, "cannot read " + pendingPath))
      return
    }
    var reason = Model.importError(text)
    if (reason !== "") {
      failed(reason)
      return
    }

    var name = pendingName
    if (name === "") {
      var base = pendingPath.substring(pendingPath.lastIndexOf("/") + 1)
      name = Model.sanitizeName(base, "amnezia")
    } else if (!Model.validName(name)) {
      name = Model.sanitizeName(name, "amnezia")
    }
    name = uniqueName(name)

    pendingName = name
    installProcess.command = ["bash", "-c",
      "umask 077; mkdir -p -- \"$1\" && install -m 600 -- \"$2\" \"$3\"",
      "_", configsDir, pendingPath, pathFor(name)]
    installProcess.running = true
  }

  function uniqueName(name) {
    if (!exists(name)) return name
    for (var suffix = 2; suffix < 100; suffix++) {
      var stem = name.substring(0, 14 - String(suffix).length).replace(/[-.]+$/, "")
      var candidate = stem + "-" + suffix
      if (!exists(candidate)) return candidate
    }
    return name
  }

  function removeConfig(name) {
    if (!Model.validName(name) || !exists(name)) {
      failed("no such config: " + name)
      return
    }
    removeProcess.command = ["rm", "-f", "--", pathFor(name)]
    removeProcess.running = true
  }

  // --------------------------------------------------------------- plumbing

  FileView {
    id: stateFile
    path: root.statePath
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadState(text())
    onLoadFailed: root.loadState("{}")
  }

  // Omarchy's own QML sticks to plain strings, so the script is a joined list
  // rather than a template literal: one less engine feature to depend on.
  readonly property string scanScript: [
    'set -u',
    'dir=$1',
    'conf=$2',
    'state=$3',
    'socket=""',
    'for candidate in /run/amneziavpn/daemon.socket /var/run/amneziavpn/daemon.socket /tmp/amneziavpn.socket; do',
    '  if [ -S "$candidate" ]; then socket="$candidate"; break; fi',
    'done',
    'printf "%s socket %s\\n" "$state" "$socket"',
    'tools=""',
    'for tool in awg-quick wg-quick pkexec; do',
    '  if command -v -- "$tool" >/dev/null 2>&1; then tools="$tools $tool"; fi',
    'done',
    'printf "%s tools%s\\n" "$state" "$tools"',
    'for file in "$dir"/*.conf; do',
    '  [ -f "$file" ] || continue',
    '  name=${file##*/}',
    '  name=${name%.conf}',
    '  status=down',
    '  rx=0',
    '  tx=0',
    '  # A live tunnel is a virtual interface named after its config. Anything',
    '  # backed by hardware, or loopback, is a name collision rather than us.',
    '  if [ -d "/sys/class/net/$name" ] && [ ! -e "/sys/class/net/$name/device" ] && [ "$name" != lo ]; then',
    '    status=up',
    '    rx=$(cat "/sys/class/net/$name/statistics/rx_bytes" 2>/dev/null || echo 0)',
    '    tx=$(cat "/sys/class/net/$name/statistics/tx_bytes" 2>/dev/null || echo 0)',
    '  fi',
    '  printf "%s %s %s %s %s\\n" "$conf" "$name" "$status" "$rx" "$tx"',
    '  cat -- "$file"',
    '  printf "\\n"',
    'done'
  ].join("\n")

  Process {
    // Everything the panel needs, in one pass: the service socket if it is
    // there, what is installed, then each config with its live state followed
    // by its text.
    id: scanProcess
    running: false
    command: ["bash", "-c", root.scanScript, "_", root.configsDir,
      "###amnezia:conf###", "###amnezia:state###"]
    stdout: StdioCollector { id: scanOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.scanning = false
      if (exitCode === 0) root.apply(scanOut.text)
      else root.lastError = "could not read " + root.configsDir
    }
  }

  Process {
    id: readProcess
    running: false
    command: []
    stdout: StdioCollector { id: readOut; waitForEnd: true }
    stderr: StdioCollector { id: readErr; waitForEnd: true }
    onExited: function(exitCode) { root.finishRead(readOut.text, exitCode, readErr.text) }
  }

  Process {
    id: installProcess
    running: false
    command: []
    stderr: StdioCollector { id: installErr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.failed(Model.commandError(exitCode, installErr.text, "could not save the config"))
        return
      }
      var name = root.pendingName
      root.imported(name)
      if (root.selected === "") root.select(name)
      root.refresh()
    }
  }

  Process {
    id: removeProcess
    running: false
    command: []
    onExited: function(exitCode) {
      if (exitCode !== 0) root.failed("could not delete the config")
      root.refresh()
    }
  }
}
