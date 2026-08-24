import QtQuick
import Quickshell.Io

// The AmneziaVPN service, spoken to directly.
//
// The official client's installer leaves that service running as root from
// boot with a socket it accepts commands on from any local process — which is
// why that client never asks for a password after install. The protocol is
// newline-delimited JSON: `status`, `activate`, `deactivate`, with `connected`
// / `disconnected` / `backendFailure` coming back.
//
// The connection is held open rather than opened per request, so the service's
// own pushes land here too: connect from the app and the panel knows without
// polling for it.
Item {
  id: root

  property string socketPath: ""
  readonly property bool available: socketPath !== ""

  property bool tunnelUp: false
  property real rxBytes: 0
  property real txBytes: 0
  property string deviceAddress: ""
  property bool busy: false

  // "" | "activate" | "deactivate": what we are waiting for, so that a
  // `disconnected` answering our own activate reads as a refusal rather than
  // as someone switching the tunnel off.
  property string awaiting: ""
  property var queue: []

  signal changed()
  signal failed(string reason)

  function poll() {
    if (available) send({ type: "status" })
  }

  function activate(payload) {
    busy = true
    awaiting = "activate"
    send(payload)
    // The service creates the interface, sets routes and DNS, then answers; it
    // is not waiting for a handshake, so this is generous rather than tight.
    answerTimer.restart()
  }

  function deactivate() {
    busy = true
    awaiting = "deactivate"
    send({ type: "deactivate" })
    answerTimer.restart()
  }

  function send(message) {
    if (!available) {
      fail("the AmneziaVPN service is not running")
      return
    }
    queue.push(JSON.stringify(message))
    if (socket.connected) flush()
    else socket.connected = true
  }

  function flush() {
    while (queue.length > 0) socket.write(queue.shift() + "\n")
    socket.flush()
  }

  function fail(reason) {
    busy = false
    awaiting = ""
    answerTimer.stop()
    queue = []
    failed(reason)
  }

  function handle(line) {
    var message
    try {
      message = JSON.parse(String(line || ""))
    } catch (error) {
      return
    }
    if (!message || typeof message !== "object") return

    switch (String(message.type || "")) {
    case "status":
      tunnelUp = message.connected === true
      rxBytes = Number(message.rxBytes || 0)
      txBytes = Number(message.txBytes || 0)
      deviceAddress = String(message.deviceIpv4Address || "")
      changed()
      break
    case "connected":
      answerTimer.stop()
      busy = false
      awaiting = ""
      tunnelUp = true
      poll()
      changed()
      break
    case "disconnected":
      answerTimer.stop()
      busy = false
      tunnelUp = false
      var refused = awaiting === "activate"
      awaiting = ""
      if (refused) {
        failed("the AmneziaVPN service rejected the config — see /var/log/AmneziaVPN")
      }
      changed()
      break
    case "backendFailure":
      fail("the AmneziaVPN service could not connect (error " + message.errorCode + ")")
      changed()
      break
    }
  }

  Socket {
    id: socket
    path: root.socketPath
    parser: SplitParser {
      onRead: function(line) { root.handle(line) }
    }
    onConnectedChanged: if (connected) root.flush()
    onError: root.fail("cannot reach the AmneziaVPN service")
  }

  Timer {
    id: answerTimer
    interval: 25000
    repeat: false
    onTriggered: root.fail("the AmneziaVPN service did not answer")
  }

  // Nothing to talk to any more: drop the connection so a later socket at the
  // same path is connected fresh.
  onAvailableChanged: {
    if (!available) {
      socket.connected = false
      tunnelUp = false
      queue = []
    }
  }
}
