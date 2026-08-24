import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Amnezia in the Omarchy bar: one switch for on/off, one list for which
// config the switch acts on. Left click opens the panel, right click on the
// bar icon toggles the tunnel without opening anything.
Panel {
  id: root
  moduleName: "io.github.rem-aster.amnezia"
  ipcTarget: "amnezia"
  manageIpc: false

  // Shield, in use elsewhere in the Omarchy shell so it is known to render;
  // the loading glyph is the shell's own spinner.
  readonly property string shieldGlyph: "󰒃"
  readonly property string loadingGlyph: "󰦖"
  readonly property string checkGlyph: "󰄬"
  readonly property string connectGlyph: "󰐊"
  readonly property string disconnectGlyph: "󰏤"

  property string focusSection: "header"
  property int configIndex: 0
  property bool cursorActive: false
  property double nowMs: Date.now()

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color hoverFill: Style.hoverFillFor(foreground, Color.accent)
  readonly property color selectedFill: Style.selectedFillFor(foreground, Color.accent)

  readonly property var activeConfig: amnezia.activeConfig
  readonly property var selectedConfig: amnezia.configFor(amnezia.selected)
  readonly property var shownConfig: activeConfig || selectedConfig
  readonly property bool headerHasCursor: cursorActive && focusSection === "header"
    && (amnezia.hasConfigs || amnezia.connected)
  readonly property string toggleHint: amnezia.connected
    ? "Disconnect " + (amnezia.activeName !== "" ? amnezia.activeName : "the tunnel")
    : (amnezia.selected === "" ? "No config to connect" : "Connect " + amnezia.selected)
  readonly property string barTooltip: amnezia.connected
    ? "Amnezia: " + (amnezia.activeName !== "" ? amnezia.activeName : "connected")
    : "Amnezia: off"

  function ensureCursor() {
    if (!amnezia.hasConfigs) {
      focusSection = "header"
      configIndex = 0
      return
    }
    if (focusSection !== "header" && focusSection !== "configs") focusSection = "configs"
    configIndex = Math.max(0, Math.min(amnezia.configs.length - 1, configIndex))
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && amnezia.hasConfigs) {
        focusSection = "configs"
        configIndex = 0
        scrollCursorIntoView()
      }
      return
    }
    if (dy < 0 && configIndex === 0) {
      setHeaderCursor()
      return
    }
    configIndex = Math.max(0, Math.min(amnezia.configs.length - 1, configIndex + dy))
    scrollCursorIntoView()
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
    if (panelFlick) panelFlick.contentY = 0
  }

  function setConfigCursor(index) {
    cursorActive = true
    focusSection = "configs"
    configIndex = index
    scrollCursorIntoView()
  }

  function currentConfig() {
    if (!amnezia.hasConfigs) return null
    return amnezia.configs[Math.max(0, Math.min(configIndex, amnezia.configs.length - 1))]
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") toggleConnection()
    else {
      var config = currentConfig()
      if (config) amnezia.select(String(config.name))
    }
  }

  function toggleConnection() {
    if (amnezia.busy) return
    if (amnezia.hasConfigs || amnezia.connected) amnezia.toggle()
  }

  function connectConfig(config) {
    if (!config || amnezia.busy) return
    if (config.up === true) amnezia.down()
    else amnezia.up(String(config.name))
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin) panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollCursorIntoView() {
    if (focusSection === "configs" && configColumn && configIndex >= 0
        && configIndex < configColumn.children.length) {
      scrollItemIntoView(configColumn.children[configIndex])
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    if (panelFlick) panelFlick.contentY = 0
    amnezia.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  onProfileIndexChanged: scrollCursorIntoView()

  Service {
    id: amnezia
    settings: root.settings
  }

  Connections {
    target: amnezia
    function onProfilesChanged() { root.ensureCursor() }
  }

  Timer {
    // Keeps the "connected · 4m" line honest while the panel is on screen.
    interval: 1000
    repeat: true
    running: root.opened && amnezia.connected
    onTriggered: root.nowMs = Date.now()
  }

  // The plugin's whole command surface. `import` and `delete` are reserved
  // words in QML, hence `add` and `remove`.
  IpcHandler {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }

    function add(path: string, name: string): string {
      if (String(path) === "") return "usage: add <path-to-.conf> [name]"
      amnezia.importConfig(path, name)
      return "importing " + path
    }

    function remove(name: string): string {
      if (String(name) === "") return "usage: remove <name>"
      amnezia.removeConfig(name)
      return "ok"
    }

    function select(name: string): string {
      if (String(name) === "") return "usage: select <name>"
      amnezia.select(name)
      return "ok"
    }

    function up(name: string): string { amnezia.up(name); return "connecting" }
    function down(): string { amnezia.down(); return "disconnecting" }
    function vpnToggle(): string { amnezia.toggle(); return "ok" }
    function refresh(): string { amnezia.refresh(); return "ok" }
    function list(): string { return amnezia.listText() }
    function status(): string { return amnezia.statusText() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: amnezia.busy ? root.loadingGlyph : root.shieldGlyph
    dimmed: !amnezia.connected && !amnezia.busy
    tooltipText: root.barTooltip
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.toggleConnection()
      else if (buttonCode === Qt.MiddleButton) amnezia.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(540))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "t" || text === "T") root.toggleConnection()
        else if (text === "r" || text === "R") amnezia.refresh()
        else if (text === "d" || text === "D") { if (amnezia.connected) amnezia.down() }
        else if (text === "c" || text === "C") root.connectConfig(root.currentConfig())
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            // The hero's own `root` is the PanelHero, so the switch reaches
            // panel state through this wrapper instead.
            readonly property bool ringVisible: root.headerHasCursor
            function focusHero() { root.setHeaderCursor() }

            PanelHero {
              id: hero
              width: parent.width
              title: "Amnezia"
              meta: Model.heroMeta({
                busy: amnezia.busy,
                connected: amnezia.connected,
                configCount: amnezia.configs.length,
                activeName: amnezia.activeName,
                since: amnezia.since
              }, root.nowMs)
              detail: root.shownConfig ? Model.protocolLabel(root.shownConfig.protocol) : ""
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconOpacity: amnezia.connected ? 1.0 : 0.5

              iconComponent: Component {
                Text {
                  text: root.shieldGlyph
                  color: amnezia.connected ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                }
              }

              trailingControl: Component {
                ToggleSwitch {
                  id: powerSwitch
                  enabled: amnezia.hasConfigs || amnezia.connected
                  opacity: amnezia.hasConfigs || amnezia.connected ? 1.0 : 0.4
                  checked: amnezia.connected
                  busy: amnezia.busy
                  hasCursor: header.ringVisible
                  foreground: hero.foreground
                  onHovered: function(on) { if (on) header.focusHero() }
                  onToggled: root.toggleConnection()

                  PanelToolTip {
                    visible: powerSwitch.containsMouse
                    text: root.toggleHint
                    fontFamily: hero.fontFamily
                  }
                }
              }
            }
          }

          Text {
            visible: text !== ""
            width: parent.width
            text: amnezia.lastError !== "" ? amnezia.lastError : amnezia.notice
            color: amnezia.lastError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            visible: amnezia.ready && amnezia.toolsMissing
            width: parent.width
            text: "Neither awg-quick nor wg-quick is installed. Install amneziawg-tools "
              + "(AUR) for Amnezia configs, or wireguard-tools for plain WireGuard."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Column {
            visible: amnezia.connected
            width: parent.width
            spacing: Style.spacing.labelGap

            InfoPair {
              label: "Config"
              // The service will happily be running something the app
              // connected to, which is not one of the configs listed below.
              value: amnezia.activeName !== "" ? amnezia.activeName : "not imported here"
            }
            InfoPair {
              label: "Endpoint"
              visible: root.activeConfig !== null
              value: root.activeConfig ? String(root.activeConfig.endpoint || "—") : "—"
            }
            InfoPair {
              label: "Address"
              visible: root.activeConfig !== null
              value: root.activeConfig ? String(root.activeConfig.address || "—") : "—"
            }
            InfoPair {
              label: "DNS"
              visible: root.activeConfig !== null && String(root.activeConfig.dns || "") !== ""
              value: root.activeConfig ? String(root.activeConfig.dns) : ""
            }
            InfoPair {
              label: "Transfer"
              value: Model.transferText(amnezia.rxBytes, amnezia.txBytes)
            }
            InfoPair {
              label: "Via"
              visible: value !== ""
              value: Model.backendLabel(amnezia.backend)
            }
          }

          PanelSeparator {
            visible: amnezia.hasConfigs
            foreground: root.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              visible: amnezia.hasConfigs
              text: "CONFIGS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              id: configColumn
              visible: amnezia.hasConfigs
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: amnezia.configs
                ConfigRow {
                  required property var modelData
                  required property int index
                  width: configColumn.width
                  config: modelData
                  rowIndex: index
                }
              }
            }

            Column {
              visible: amnezia.ready && !amnezia.hasConfigs
              width: parent.width
              spacing: Style.space(6)

              Text {
                width: parent.width
                text: "No configs yet"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                horizontalAlignment: Text.AlignHCenter
              }

              Text {
                width: parent.width
                text: "Add one with\nomarchy-shell amnezia add <file.conf>"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
              }
            }
          }

          Text {
            visible: amnezia.hasConfigs
            width: parent.width
            text: "enter pick · c connect · t toggle · r refresh"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }

  component ConfigRow: CursorSurface {
    id: configRow
    property var config: null
    property int rowIndex: 0

    readonly property string configName: config ? String(config.name || "") : ""
    readonly property bool isActive: config ? config.up === true : false
    readonly property bool isSelected: configName !== "" && configName === amnezia.selected
    readonly property string missingTool: Model.missingToolFor(config, amnezia.backend, amnezia.tools)

    hasCursor: root.cursorActive && root.focusSection === "configs" && root.configIndex === rowIndex
    current: isSelected
    foreground: root.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill

    implicitHeight: rowContent.implicitHeight + Style.spacing.xl

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setConfigCursor(configRow.rowIndex)
      onClicked: amnezia.select(configRow.configName)
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: root.shieldGlyph
        color: configRow.isActive ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rowContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          Layout.fillWidth: true
          text: configRow.configName
          color: configRow.missingTool === "" ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          Layout.fillWidth: true
          text: Model.configMeta(configRow.config, amnezia.backend, amnezia.tools)
          color: configRow.missingTool === "" ? root.dim : root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Text {
        visible: configRow.isActive
        text: root.checkGlyph
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      PanelActionButton {
        iconText: configRow.isActive ? root.disconnectGlyph : root.connectGlyph
        tooltipText: configRow.isActive ? "Disconnect" : "Connect"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !amnezia.busy && configRow.missingTool === ""
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.connectConfig(configRow.config)
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    InfoLabel { text: label }

    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth
        - parent.children[2].implicitWidth - parent.spacing * 2)
      height: 1
    }

    InfoValue { text: value }
  }

  component InfoLabel: Text {
    color: root.foreground
    opacity: 0.6
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component InfoValue: Text {
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    elide: Text.ElideRight
  }
}
