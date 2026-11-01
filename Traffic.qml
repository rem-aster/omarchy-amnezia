import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Tunnel throughput: the two rates, a one-minute sparkline, and the session's
// totals. Drawn in the panel's foreground only — download as a filled area,
// upload as a thinner, fainter line — so the two read apart by shape on every
// theme rather than by a colour the theme may not have.
Item {
  id: root

  property var rxHistory: []
  property var txHistory: []
  property real rxRate: 0
  property real txRate: 0
  property real rxTotal: 0
  property real txTotal: 0
  property string uptime: ""
  property int samples: 60

  property color foreground: Commons.Color.foreground
  property string fontFamily: Style.font.family
  readonly property color dim: Qt.darker(foreground, 1.55)

  property int hoverIndex: -1

  implicitHeight: column.implicitHeight

  onRxHistoryChanged: chart.requestPaint()
  onTxHistoryChanged: chart.requestPaint()
  onForegroundChanged: chart.requestPaint()

  Column {
    id: column
    width: parent.width
    spacing: Style.space(6)

    Row {
      spacing: Style.space(18)

      Text {
        text: "↓ " + Model.formatRate(root.rxRate)
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        text: "↑ " + Model.formatRate(root.txRate)
        color: root.foreground
        opacity: 0.75
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
    }

    Item {
      width: parent.width
      height: Style.space(36)

      Canvas {
        id: chart
        anchors.fill: parent
        antialiasing: true

        // CSS strings: what Context2D is documented to take for a style.
        function ink(alpha) {
          var c = root.foreground
          return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + ","
            + Math.round(c.b * 255) + "," + alpha + ")"
        }

        function peak() {
          var top = 1024
          for (var i = 0; i < root.rxHistory.length; i++) top = Math.max(top, root.rxHistory[i])
          for (var j = 0; j < root.txHistory.length; j++) top = Math.max(top, root.txHistory[j])
          return top
        }

        // Newest sample at the right edge; the line grows in from the right.
        function xAt(slot) {
          return root.samples <= 1 ? width : slot / (root.samples - 1) * width
        }

        function yAt(value, top) {
          return height - 1.5 - Math.max(0, Math.min(1, value / top)) * (height - 4)
        }

        function trace(ctx, series, top) {
          var offset = root.samples - series.length
          for (var i = 0; i < series.length; i++) {
            var x = xAt(offset + i)
            var y = yAt(series[i], top)
            if (i === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
          }
        }

        onPaint: {
          var ctx = getContext("2d")
          ctx.reset()
          ctx.fillStyle = ink(0.18)
          ctx.fillRect(0, height - 1, width, 1)
          var top = peak()
          var rx = root.rxHistory
          if (rx.length > 1) {
            ctx.beginPath()
            ctx.moveTo(xAt(root.samples - rx.length), height)
            var offset = root.samples - rx.length
            for (var i = 0; i < rx.length; i++) ctx.lineTo(xAt(offset + i), yAt(rx[i], top))
            ctx.lineTo(width, height)
            ctx.closePath()
            ctx.fillStyle = ink(0.14)
            ctx.fill()

            ctx.beginPath()
            trace(ctx, rx, top)
            ctx.strokeStyle = ink(1.0)
            ctx.lineWidth = 2
            ctx.lineJoin = "round"
            ctx.stroke()
          }
          var tx = root.txHistory
          if (tx.length > 1) {
            ctx.beginPath()
            trace(ctx, tx, top)
            ctx.strokeStyle = ink(0.55)
            ctx.lineWidth = 1.2
            ctx.stroke()
          }
          if (root.hoverIndex >= 0 && root.hoverIndex < rx.length) {
            var hx = xAt(root.samples - rx.length + root.hoverIndex)
            ctx.fillStyle = ink(0.45)
            ctx.fillRect(Math.round(hx), 0, 1, height)
          }
        }
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: function(mouse) {
          var n = root.rxHistory.length
          var slot = Math.round(mouse.x / Math.max(1, width) * (root.samples - 1))
          var index = slot - (root.samples - n)
          root.hoverIndex = index >= 0 && index < n ? index : -1
          chart.requestPaint()
        }
        onExited: {
          root.hoverIndex = -1
          chart.requestPaint()
        }
      }
    }

    // Hovering the chart reads that second out in the same place the totals
    // sit, so nothing jumps.
    Text {
      width: parent.width
      text: {
        if (root.hoverIndex >= 0 && root.hoverIndex < root.rxHistory.length) {
          var ago = root.rxHistory.length - 1 - root.hoverIndex
          return (ago === 0 ? "now" : ago + "s ago") + "   ↓ " + Model.formatRate(root.rxHistory[root.hoverIndex])
            + "   ↑ " + Model.formatRate(root.txHistory[root.hoverIndex] || 0)
        }
        return "Session  ↓ " + Model.formatBytes(root.rxTotal) + "   ↑ " + Model.formatBytes(root.txTotal)
          + (root.uptime !== "" ? "   ·  up " + root.uptime : "")
      }
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }
  }
}
