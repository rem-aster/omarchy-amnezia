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

  // The CLI ships next to this file, so the widget works from wherever the
  // plugin was cloned to without a PATH entry or a hardcoded plugin id.
  readonly property string cliPath: {
    var url = String(Qt.resolvedUrl("bin/omarchy-amnezia"))
    if (url.indexOf("file://") === 0) url = url.substring(7)
    return decodeURIComponent(url)
  }

  property string focusSection: "header"
  property int profileIndex: 0
  property bool cursorActive: false
  property double nowMs: Date.now()

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color hoverFill: Style.hoverFillFor(foreground, Color.accent)
  readonly property color selectedFill: Style.selectedFillFor(foreground, Color.accent)

  readonly property var activeEntry: amnezia.profileByName(amnezia.activeProfile)
  readonly property var selectedEntry: amnezia.profileByName(amnezia.selected)
  readonly property var shownEntry: activeEntry || selectedEntry
  readonly property bool headerHasCursor: cursorActive && focusSection === "header" && amnezia.hasProfiles
  readonly property string toggleHint: amnezia.connected
    ? "Disconnect " + amnezia.activeProfile
    : (amnezia.selected === "" ? "No config to connect" : "Connect " + amnezia.selected)
  readonly property string barTooltip: amnezia.connected
    ? "Amnezia: " + amnezia.activeProfile
    : "Amnezia: off"

  function ensureCursor() {
    if (!amnezia.hasProfiles) {
      focusSection = "header"
      profileIndex = 0
      return
    }
    if (focusSection !== "header" && focusSection !== "profiles") focusSection = "profiles"
    profileIndex = Math.max(0, Math.min(amnezia.profiles.length - 1, profileIndex))
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && amnezia.hasProfiles) {
        focusSection = "profiles"
        profileIndex = 0
        scrollCursorIntoView()
      }
      return
    }
    if (dy < 0 && profileIndex === 0) {
      setHeaderCursor()
      return
    }
    profileIndex = Math.max(0, Math.min(amnezia.profiles.length - 1, profileIndex + dy))
    scrollCursorIntoView()
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
    if (panelFlick) panelFlick.contentY = 0
  }

  function setProfileCursor(index) {
    cursorActive = true
    focusSection = "profiles"
    profileIndex = index
    scrollCursorIntoView()
  }

  function currentProfile() {
    if (!amnezia.hasProfiles) return null
    return amnezia.profiles[Math.max(0, Math.min(profileIndex, amnezia.profiles.length - 1))]
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") toggleConnection()
    else {
      var profile = currentProfile()
      if (profile) amnezia.select(String(profile.name))
    }
  }

  function toggleConnection() {
    if (!amnezia.busy && amnezia.hasProfiles) amnezia.toggle()
  }

  function connectProfile(profile) {
    if (!profile || amnezia.busy) return
    if (profile.active === true) amnezia.down()
    else amnezia.up(String(profile.name))
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
    if (focusSection === "profiles" && profileColumn && profileIndex >= 0
        && profileIndex < profileColumn.children.length) {
      scrollItemIntoView(profileColumn.children[profileIndex])
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
    cliPath: root.cliPath
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

  IpcHandler {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { amnezia.refresh(); return "ok" }
    function vpnUp(name: string): string { amnezia.up(name); return "ok" }
    function vpnDown(): string { amnezia.down(); return "ok" }
    function vpnToggle(): string { amnezia.toggle(); return "ok" }
    function pick(name: string): string { amnezia.select(name); return "ok" }
    function status(): string {
      return amnezia.connected ? "connected " + amnezia.activeProfile : "disconnected"
    }
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
        else if (text === "c" || text === "C") root.connectProfile(root.currentProfile())
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
              meta: Model.heroMeta(amnezia.status, amnezia.busy, amnezia.profiles.length, root.nowMs)
              detail: root.shownEntry ? Model.protocolLabel(root.shownEntry.protocol) : ""
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
                  enabled: amnezia.hasProfiles
                  opacity: amnezia.hasProfiles ? 1.0 : 0.4
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
            text: amnezia.lastError !== "" ? amnezia.lastError : amnezia.actionStatus
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
            visible: amnezia.connected && root.activeEntry !== null
            width: parent.width
            spacing: Style.spacing.labelGap

            InfoPair { label: "Config"; value: amnezia.activeProfile }
            InfoPair {
              label: "Endpoint"
              value: root.activeEntry ? String(root.activeEntry.endpoint || "—") : "—"
            }
            InfoPair {
              label: "Address"
              value: root.activeEntry ? String(root.activeEntry.address || "—") : "—"
            }
            InfoPair {
              label: "DNS"
              visible: root.activeEntry !== null && String(root.activeEntry.dns || "") !== ""
              value: root.activeEntry ? String(root.activeEntry.dns) : ""
            }
            InfoPair {
              label: "Transfer"
              value: Model.transferText(amnezia.status.rxBytes, amnezia.status.txBytes)
            }
          }

          PanelSeparator {
            visible: amnezia.hasProfiles
            foreground: root.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              visible: amnezia.hasProfiles
              text: "CONFIGS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              id: profileColumn
              visible: amnezia.hasProfiles
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: amnezia.profiles
                ProfileRow {
                  required property var modelData
                  required property int index
                  width: profileColumn.width
                  profile: modelData
                  rowIndex: index
                }
              }
            }

            Column {
              visible: amnezia.ready && !amnezia.hasProfiles
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
                text: "Import one with\nomarchy-amnezia import <file|vpn://…>"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
              }
            }
          }

          Text {
            visible: amnezia.hasProfiles
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

  component ProfileRow: CursorSurface {
    id: profileRow
    property var profile: null
    property int rowIndex: 0

    readonly property string profileName: profile ? String(profile.name || "") : ""
    readonly property bool isActive: profile ? profile.active === true : false
    readonly property bool isSelected: profileName !== "" && profileName === amnezia.selected
    readonly property string missingTool: Model.missingToolFor(profile, amnezia.tools)

    hasCursor: root.cursorActive && root.focusSection === "profiles" && root.profileIndex === rowIndex
    current: isSelected
    foreground: root.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill

    implicitHeight: rowContent.implicitHeight + Style.spacing.xl

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setProfileCursor(profileRow.rowIndex)
      onClicked: amnezia.select(profileRow.profileName)
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
        color: profileRow.isActive ? root.foreground : root.dim
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
          text: profileRow.profileName
          color: profileRow.missingTool === "" ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          Layout.fillWidth: true
          text: Model.profileMeta(profileRow.profile, amnezia.tools)
          color: profileRow.missingTool === "" ? root.dim : root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Text {
        visible: profileRow.isActive
        text: root.checkGlyph
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      PanelActionButton {
        iconText: profileRow.isActive ? root.disconnectGlyph : root.connectGlyph
        tooltipText: profileRow.isActive ? "Disconnect" : "Connect"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !amnezia.busy && profileRow.missingTool === ""
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.connectProfile(profileRow.profile)
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
