import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Amnezia Premium in the Omarchy bar: a shield in the bar, and a panel built
// from the same pieces as the Wi-Fi, Bluetooth and Tailscale panels. The hero
// carries the one switch; under it the connection, its traffic, the protocol,
// and two tabs — the locations your subscription offers, and the account
// with its device slots.
//
// Left click opens the panel, right click connects to the picked location or
// disconnects, middle click re-reads the state.
Panel {
  id: root
  moduleName: "rem-aster.amnezia"
  ipcTarget: "amnezia"
  manageIpc: false

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

  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property string shieldOn: String.fromCodePoint(0xF0565)
  readonly property string shieldOff: String.fromCodePoint(0xF0498)
  readonly property string loadingGlyph: "󰦖"
  readonly property string checkGlyph: "󰄬"
  readonly property string chevronOpen: String.fromCodePoint(0xF0140)
  readonly property string chevronClosed: String.fromCodePoint(0xF0142)
  readonly property string pinGlyph: String.fromCodePoint(0xF034E)
  readonly property string refreshGlyph: String.fromCodePoint(0xF0450)
  readonly property string keyGlyph: String.fromCodePoint(0xF0306)
  readonly property string renewGlyph: String.fromCodePoint(0xF006A)
  readonly property string openGlyph: String.fromCodePoint(0xF03CC)
  readonly property string forgetGlyph: String.fromCodePoint(0xF0343)
  readonly property string laptopGlyph: String.fromCodePoint(0xF0322)
  readonly property string phoneGlyph: String.fromCodePoint(0xF011C)

  readonly property bool showLocationCode: String(setting("showLocation", "On")) !== "Off"
  readonly property bool rightClickToggles: String(setting("rightClick", "Toggle")) !== "Nothing"

  // ── Keyboard cursor ────────────────────────────────────────────────────
  // One cursor walks every stop top to bottom. `stops` lists the ones on
  // screen right now; each row asks `at(section, index)` whether it is the
  // one, so the cursor follows whatever is folded, filtered or on the other
  // tab without each section keeping its own bookkeeping.
  property bool cursorActive: false
  property bool keyboardMoved: false
  property int cursorStop: 0
  property int protocolCursor: 0
  property string tab: "locations"
  property bool recentExpanded: true
  property bool locationsExpanded: true
  property bool devicesExpanded: true
  property bool keyEntryOpen: false
  property bool keySaving: false
  property string filterText: ""
  property double nowMs: Date.now()
  property string confirmKind: ""
  property var confirmDevice: null
  property real pointerX: -1
  property real pointerY: -1

  readonly property bool showKeyEntry: amnezia.ready && (!amnezia.hasKey || keyEntryOpen)
  readonly property bool showMain: amnezia.ready && amnezia.hasKey && !keyEntryOpen
  readonly property var filteredCountries: Model.filterCountries(amnezia.countries, filterText)
  readonly property var recentRows: Model.recentRows(amnezia.recent, amnezia.countries)
  readonly property string pickedName: amnezia.state.picked.country !== ""
    ? amnezia.placeName(amnezia.state.picked.country) : ""
  readonly property var accountActions: {
    var list = [{ key: "renewal", icon: openGlyph, title: "Renew the subscription",
                  detail: "Opens Amnezia's renewal page in the browser" }]
    if (pickedName !== "") {
      list.push({ key: "renew", icon: renewGlyph, title: "New config for " + pickedName,
                  detail: "A fresh " + Model.protocolLabel(amnezia.protocol) + " key — the old one stops working" })
    }
    // Its "checked 2m ago" is filled in by the row, so the list itself only
    // changes when the picked location or protocol does.
    list.push({ key: "refresh", icon: refreshGlyph, title: "Refresh from Amnezia", detail: "" })
    list.push({ key: "key", icon: keyGlyph, title: "Replace the key", detail: "Paste another Premium key" })
    list.push({ key: "forget", icon: forgetGlyph, title: "Forget the key on this computer",
                detail: "Disconnects and gives this computer's device slot back" })
    return list
  }

  readonly property var stops: {
    var s = []
    if (!amnezia.ready) return s
    if (showKeyEntry) {
      s.push({ section: "keySave", index: 0 })
      if (keyEntryOpen) s.push({ section: "keyCancel", index: 0 })
      return s
    }
    s.push({ section: "header", index: 0 })
    s.push({ section: "protocol", index: 0 })
    s.push({ section: "tabs", index: 0 })
    if (tab === "locations") {
      if (recentRows.length > 0) {
        s.push({ section: "recentHeader", index: 0 })
        if (recentExpanded) for (var i = 0; i < recentRows.length; i++) s.push({ section: "recent", index: i })
      }
      s.push({ section: "locationsHeader", index: 0 })
      if (locationsExpanded) for (var j = 0; j < filteredCountries.length; j++) s.push({ section: "locations", index: j })
    } else {
      for (var a = 0; a < accountActions.length; a++) s.push({ section: "account", index: a })
      if (amnezia.devices.length > 0) {
        s.push({ section: "devicesHeader", index: 0 })
        if (devicesExpanded) for (var d = 0; d < amnezia.devices.length; d++) s.push({ section: "devices", index: d })
      }
    }
    return s
  }
  readonly property var stop: stops.length > 0
    ? stops[Math.max(0, Math.min(cursorStop, stops.length - 1))] : ({ section: "", index: 0 })

  readonly property string checkedText: amnezia.accountRefreshing ? "Asking Amnezia…"
    : (amnezia.state.account.fetchedAt > 0
       ? "Checked " + Model.formatAgo(amnezia.state.account.fetchedAt, nowMs) : "Not asked yet")

  readonly property string statusText: amnezia.actionStatus !== "" ? amnezia.actionStatus
    : (amnezia.lastError !== "" ? amnezia.lastError : amnezia.notice)
  readonly property bool statusIsError: amnezia.actionStatus === "" && amnezia.lastError !== ""
  readonly property var protocolNote: Model.protocolNote(amnezia.state, amnezia.protocol)

  function at(section, index) {
    return cursorActive && stop.section === section && stop.index === (index === undefined ? 0 : index)
  }

  function setCursor(section, index) {
    var wanted = index === undefined ? 0 : index
    for (var i = 0; i < stops.length; i++) {
      if (stops[i].section === section && stops[i].index === wanted) {
        cursorStop = i
        cursorActive = true
        if (section === "protocol") protocolCursor = Math.max(0, Model.protocols.indexOf(amnezia.protocol))
        return
      }
    }
  }

  // Hover moves the cursor only when the pointer itself moved: rows sliding
  // under a still pointer while the keyboard scrolls must not drag the
  // cursor back under it.
  function hoverCursor(area, mouse, section, index) {
    var point = area.mapToGlobal(mouse.x, mouse.y)
    if (Math.abs(point.x - pointerX) < 1 && Math.abs(point.y - pointerY) < 1) return
    pointerX = point.x
    pointerY = point.y
    keyboardMoved = false
    setCursor(section, index)
  }

  function moveCursor(dx, dy) {
    keyboardMoved = true
    if (dy !== 0) {
      cursorStop = Math.max(0, Math.min(stops.length - 1, cursorStop + dy))
      if (stop.section === "protocol") protocolCursor = Math.max(0, Model.protocols.indexOf(amnezia.protocol))
      return
    }
    var section = stop.section
    if (section === "protocol") protocolCursor = dx > 0 ? 1 : 0
    else if (section === "tabs") setTab(dx > 0 ? "account" : "locations")
    else if (section === "recentHeader") recentExpanded = dx > 0
    else if (section === "locationsHeader") locationsExpanded = dx > 0
    else if (section === "devicesHeader") devicesExpanded = dx > 0
  }

  function activateCursor() {
    var section = stop.section
    var index = stop.index
    if (section === "header") amnezia.toggle()
    else if (section === "protocol") amnezia.setProtocol(Model.protocols[protocolCursor])
    else if (section === "tabs") setTab(tab === "locations" ? "account" : "locations")
    else if (section === "recentHeader") recentExpanded = !recentExpanded
    else if (section === "recent" && recentRows[index]) amnezia.connectTo(recentRows[index].entry.code, recentRows[index].protocol)
    else if (section === "locationsHeader") locationsExpanded = !locationsExpanded
    else if (section === "locations" && filteredCountries[index]) amnezia.connectTo(filteredCountries[index].code, "")
    else if (section === "account" && accountActions[index]) runAccountAction(accountActions[index].key)
    else if (section === "devicesHeader") devicesExpanded = !devicesExpanded
    else if (section === "devices" && amnezia.devices[index]) askRevoke(amnezia.devices[index])
    else if (section === "keySave") saveKey()
    else if (section === "keyCancel") closeKeyEntry()
  }

  function setTab(next) {
    if (tab === next) return
    tab = next
    setCursor("tabs", 0)
    if (next === "account" && amnezia.state.account.fetchedAt * 1000 < Date.now() - 60000) amnezia.refreshAccount()
  }

  function textKey(t) {
    if (t === "/" && showMain) {
      if (tab !== "locations") setTab("locations")
      locationsExpanded = true
      Qt.callLater(function() { filterField.forceActiveFocus() })
    } else if (t === "r" || t === "R") {
      amnezia.refresh()
      amnezia.refreshAccount()
    } else if (t === "t" || t === "T") {
      if (showMain) setTab(tab === "locations" ? "account" : "locations")
    } else if (t === "q" || t === "Q") {
      root.close()
    }
  }

  function runAccountAction(key) {
    if (key === "renewal") amnezia.openRenewal()
    else if (key === "renew") askConfirm("renew", null)
    else if (key === "refresh") amnezia.refreshAccount()
    else if (key === "key") openKeyEntry()
    else if (key === "forget") askConfirm("forget", null)
  }

  function askRevoke(device) {
    if (!device || device.uuid === "") return
    askConfirm("revoke", device)
  }

  function askConfirm(kind, device) {
    confirmKind = kind
    confirmDevice = device
    confirm.selectedIndex = 0
    confirm.opened = true
  }

  function confirmed() {
    confirm.opened = false
    if (confirmKind === "revoke" && confirmDevice) amnezia.revokeDevice(confirmDevice.uuid)
    else if (confirmKind === "forget") amnezia.forgetKey()
    else if (confirmKind === "renew") amnezia.renewConfig()
    confirmKind = ""
    confirmDevice = null
  }

  readonly property string confirmMessage: {
    if (confirmKind === "revoke" && confirmDevice) {
      return confirmDevice.me
        ? "Free this computer's own slot? Its configs stop working until it gets new ones."
        : "Free the slot of " + Model.deviceName(confirmDevice) + "? That device's configs stop working, and the slot is yours to use again."
    }
    if (confirmKind === "forget")
      return "Disconnect, give this computer's device slot back, and forget the key here? You can paste it again later."
    if (confirmKind === "renew")
      return "Ask Amnezia for a new config for " + pickedName + "? The current one stops working, and a fresh key can take a minute or two to carry traffic."
    return ""
  }

  function openKeyEntry() {
    keyEntryOpen = true
    keyField.text = ""
    setCursor("keySave", 0)
    Qt.callLater(function() { keyField.forceActiveFocus() })
  }

  function closeKeyEntry() {
    keyEntryOpen = false
    keyField.text = ""
    keyCatcher.forceActiveFocus()
    setCursor("tabs", 0)
  }

  function saveKey() {
    if (amnezia.saveKey(keyField.text)) keySaving = true
  }

  function scrollIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < panelFlick.contentY + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > panelFlick.contentY + panelFlick.height - margin)
        panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: {
    amnezia.panelOpen = opened
    if (!opened) return
    cursorActive = false
    keyboardMoved = false
    keyEntryOpen = false
    confirm.opened = false
    filterField.text = ""
    nowMs = Date.now()
    if (panelFlick) panelFlick.contentY = 0
    amnezia.refresh()
    if (amnezia.hasKey && amnezia.state.account.fetchedAt * 1000 < Date.now() - 3600000) amnezia.refreshAccount()
    Qt.callLater(function() {
      if (root.showKeyEntry) keyField.forceActiveFocus()
      else keyCatcher.forceActiveFocus()
    })
  }

  onFilterTextChanged: {
    var first = stops.length
    for (var i = 0; i < stops.length; i++) if (stops[i].section === "locations") { first = i; break }
    if (first < stops.length && cursorActive) cursorStop = first
  }

  Service {
    id: amnezia
    cliPath: root.cliPath
    settings: root.settings
  }

  // A saved key closes the entry; a refused one leaves it open with the
  // reason above it.
  Connections {
    target: amnezia
    function onWorkingChanged() {
      if (amnezia.working || !root.keySaving) return
      root.keySaving = false
      if (amnezia.lastError === "") {
        keyField.text = ""
        root.keyEntryOpen = false
        root.tab = "locations"
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      }
    }
  }

  Timer {
    // Keeps "connected · 4m" and "checked 2m ago" honest while the panel is
    // on screen.
    interval: 1000
    repeat: true
    running: root.opened
    onTriggered: root.nowMs = Date.now()
  }

  ShellIpc {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { amnezia.refresh() }
    function up(code: string): string {
      if (!amnezia.hasKey) return "no key"
      if (String(code) === "") amnezia.toggle()
      else amnezia.connectTo(String(code).toLowerCase(), "")
      return "connecting"
    }
    function down(): string { amnezia.disconnect(); return "disconnecting" }
    function vpnToggle(): string { amnezia.toggle(); return "ok" }
    function protocol(name: string): string { amnezia.setProtocol(String(name)); return "ok" }
    function status(): string {
      return Model.barTooltip(amnezia.state, amnezia.actionStatus, amnezia.lastError, Date.now()).replace(/^Amnezia: /, "")
    }
  }

  readonly property bool barVertical: bar ? bar.vertical === true : false
  readonly property string barLabel: showLocationCode && amnezia.state.connected && !amnezia.state.foreign && !barVertical
    ? amnezia.state.country.split("-")[0].toUpperCase() : ""

  // BarIconButton is one icon wide whatever it holds, and draws its label
  // beside the icon: a country code would spill over both edges and eat the
  // gap to the neighbours. The slot grows by exactly what the label adds —
  // its width and the 4px the button puts between label and icon — so the
  // padding on each side stays that of every other icon in the bar.
  TextMetrics {
    id: barLabelMetrics
    font.family: button.fontFamily
    font.pixelSize: button.fontSize
    text: root.barLabel
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: amnezia.working ? root.loadingGlyph : (amnezia.connected ? root.shieldOn : root.shieldOff)
    labelText: root.barLabel
    slotSize: Style.bar.iconSlot
      + (root.barLabel !== "" ? Math.ceil(barLabelMetrics.advanceWidth) + Style.space(4) : 0)
    dimmed: !amnezia.connected && !amnezia.working
    tooltipText: Model.barTooltip(amnezia.state, amnezia.actionStatus, amnezia.lastError, root.nowMs)
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton && root.rightClickToggles) {
        if (amnezia.hasKey) amnezia.toggle()
        else root.open()
      } else if (buttonCode === Qt.MiddleButton) {
        amnezia.refresh()
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // A text field owns the keyboard while it has focus, or every letter
      // typed would move the cursor instead.
      blocked: filterField.activeFocus || keyField.activeFocus
      onMoveRequested: function(dx, dy) {
        // A dialog over the dimmed panel owns the keyboard.
        if (confirm.opened) {
          if (dx !== 0) confirm.selectedIndex = confirm.selectedIndex === 0 ? 1 : 0
          return
        }
        if (!root.cursorActive) {
          root.cursorActive = true
          root.keyboardMoved = true
          return
        }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: {
        if (confirm.opened) {
          if (confirm.selectedIndex === 0) confirm.canceled()
          else root.confirmed()
          return
        }
        if (root.cursorActive) root.activateCursor()
      }
      onCloseRequested: {
        if (confirm.opened) { confirm.canceled(); return }
        if (root.keyEntryOpen) { root.closeKeyEntry(); return }
        if (root.filterText !== "") { filterField.text = ""; return }
        root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      // No single letter touches the tunnel: the panel takes the keyboard
      // when it opens, and a stray key must not change the connection.
      onTextKey: function(t) { if (!confirm.opened) root.textKey(t) }

      ConfirmDialog {
        id: confirm
        anchors.fill: parent
        z: 100
        message: root.confirmMessage
        cancelText: "Cancel"
        confirmText: root.confirmKind === "revoke" ? "Free the slot"
          : (root.confirmKind === "forget" ? "Forget the key" : "Get a new config")
        foreground: root.foreground
        fontFamily: root.fontFamily
        onCanceled: { opened = false; root.confirmKind = ""; root.confirmDevice = null }
        onConfirmed: root.confirmed()
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

          // ── Hero ─────────────────────────────────────────────────────
          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            // The hero's components resolve `root` to PanelHero, so they
            // reach panel state through this item instead.
            readonly property bool ringVisible: root.at("header")
            readonly property bool switchVisible: amnezia.hasKey && !root.keyEntryOpen
            readonly property string toggleHint: amnezia.connected ? "Disconnect"
              : (root.pickedName !== "" ? "Connect to " + root.pickedName : "Connect")
            readonly property color iconColor: amnezia.connected ? root.foreground : root.dim
            readonly property string iconText: amnezia.connected ? root.shieldOn : root.shieldOff
            readonly property string glyphFont: root.fontFamily
            function focusHero() { root.setCursor("header", 0) }

            PanelHero {
              id: hero
              width: parent.width
              title: "Amnezia Premium"
              meta: Model.heroMeta(amnezia.state, amnezia.actionStatus, root.nowMs)
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconOpacity: amnezia.connected ? 1.0 : 0.5

              iconComponent: Component {
                Text {
                  text: header.iconText
                  color: header.iconColor
                  font.family: header.glyphFont
                  font.pixelSize: Style.font.display
                }
              }

              trailingControl: Component {
                ToggleSwitch {
                  id: powerSwitch
                  visible: header.switchVisible
                  checked: amnezia.connected
                  busy: amnezia.working
                  hasCursor: header.ringVisible
                  foreground: hero.foreground
                  onHovered: function(on) { if (on) header.focusHero() }
                  onToggled: amnezia.toggle()

                  PanelToolTip {
                    visible: powerSwitch.containsMouse
                    text: header.toggleHint
                    fontFamily: hero.fontFamily
                  }
                }
              }
            }
          }

          Text {
            visible: root.statusText !== ""
            width: parent.width
            text: root.statusText
            textFormat: Text.PlainText
            color: root.statusIsError ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // ── First run, or a new key ─────────────────────────────────
          Column {
            visible: root.showKeyEntry
            width: parent.width
            spacing: Style.space(8)

            Text {
              width: parent.width
              text: root.keyEntryOpen ? "Paste the new Premium key" : "Paste your Amnezia Premium key"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }

            Text {
              width: parent.width
              text: "It starts with vpn:// — in the Amnezia app open your subscription and use Share, "
                + "or take it from the e-mail that came with the purchase."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            TextField {
              id: keyField
              width: parent.width
              foreground: root.foreground
              password: true
              placeholderText: "vpn://…"
              Keys.onReturnPressed: function(event) { root.saveKey(); event.accepted = true }
              Keys.onEnterPressed: function(event) { root.saveKey(); event.accepted = true }
              Keys.onEscapePressed: function(event) {
                if (root.keyEntryOpen) root.closeKeyEntry()
                else keyCatcher.forceActiveFocus()
                event.accepted = true
              }
            }

            ActionRow {
              width: parent.width
              section: "keySave"
              icon: root.keyGlyph
              title: amnezia.working ? "Checking the key…" : "Save the key"
              subtitle: "Kept in ~/.config/omarchy/amnezia, readable only by you"
              enabled: !amnezia.working
              onClicked: root.saveKey()
            }

            ActionRow {
              visible: root.keyEntryOpen
              width: parent.width
              section: "keyCancel"
              icon: ""
              title: "Cancel"
              subtitle: "Keep the key you have"
              onClicked: root.closeKeyEntry()
            }
          }

          // ── Connection ───────────────────────────────────────────────
          Column {
            visible: root.showMain && amnezia.state.connected
            width: parent.width
            spacing: Style.spacing.labelGap

            readonly property var cfg: Model.config(amnezia.state, amnezia.state.country, amnezia.state.protocol)

            InfoPair {
              label: "Location"
              value: amnezia.state.foreign ? "another app's tunnel" : amnezia.placeName(amnezia.state.country)
            }
            InfoPair {
              label: "Protocol"
              value: Model.protocolLabel(amnezia.state.protocol)
                + (amnezia.state.backendLabel !== "" ? " · " + amnezia.state.backendLabel : "")
            }
            InfoPair {
              visible: parent.cfg !== null && parent.cfg.endpoint !== ""
              label: "Server"
              value: parent.cfg ? parent.cfg.endpoint : ""
            }
            InfoPair {
              visible: parent.cfg !== null && parent.cfg.address !== ""
              label: "Address"
              value: parent.cfg ? parent.cfg.address : ""
            }
            InfoPair {
              visible: parent.cfg !== null && parent.cfg.transport !== ""
              label: "Transport"
              value: parent.cfg ? parent.cfg.transport : ""
            }
            InfoPair {
              visible: parent.cfg !== null && parent.cfg.dns !== ""
              label: "DNS"
              value: parent.cfg ? parent.cfg.dns : ""
            }
          }

          // Shown from the first moment of a live tunnel, before two samples
          // exist to draw: appearing a second later would push everything
          // under it down, and the keyboard cursor with it.
          Traffic {
            visible: root.showMain && amnezia.state.connected && !amnezia.state.foreign && !amnezia.working
            width: parent.width
            rxHistory: amnezia.rxHistory
            txHistory: amnezia.txHistory
            rxRate: amnezia.rxRate
            txRate: amnezia.txRate
            rxTotal: amnezia.rxTotal
            txTotal: amnezia.txTotal
            uptime: Model.uptime(amnezia.state, root.nowMs)
            samples: amnezia.trafficSamples
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          PanelSeparator {
            visible: root.showMain
            foreground: root.foreground
          }

          // ── Protocol ─────────────────────────────────────────────────
          Column {
            visible: root.showMain
            width: parent.width
            spacing: Style.space(6)

            Item {
              width: parent.width
              implicitHeight: protocolGroup.implicitHeight

              Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "Protocol"
                color: root.foreground
                opacity: 0.6
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              ButtonGroup {
                id: protocolGroup
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                options: [{ value: "awg", label: "AmneziaWG" }, { value: "vless", label: "VLESS" }]
                value: amnezia.protocol
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                focusable: false
                cursorIndex: root.at("protocol") ? root.protocolCursor : -1
                onChanged: function(v) { amnezia.setProtocol(v) }
                onHovered: function(index, isHovered) {
                  if (!isHovered) return
                  root.setCursor("protocol", 0)
                  root.protocolCursor = index
                }
              }
            }

            Text {
              width: parent.width
              text: root.protocolNote.text
              color: root.protocolNote.level === "missing" ? root.urgent : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          // ── Tabs ─────────────────────────────────────────────────────
          Row {
            id: tabRow
            visible: root.showMain
            width: parent.width
            spacing: Style.space(6)
            readonly property real cellWidth: (width - spacing) / 2

            TabPill { width: tabRow.cellWidth; tabKey: "locations"; text: "Locations" }
            TabPill { width: tabRow.cellWidth; tabKey: "account"; text: "Account" }
          }

          // ── Locations ────────────────────────────────────────────────
          Column {
            visible: root.showMain && root.tab === "locations" && root.recentRows.length > 0
            width: parent.width
            spacing: Style.space(6)

            FoldHeader {
              section: "recentHeader"
              title: "RECENT"
              count: root.recentRows.length
              expanded: root.recentExpanded
              onToggled: root.recentExpanded = !root.recentExpanded
            }

            Column {
              id: recentColumn
              visible: root.recentExpanded
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: root.recentRows
                LocationRow {
                  required property var modelData
                  required property int index
                  width: recentColumn.width
                  section: "recent"
                  rowIndex: index
                  entry: modelData.entry
                  pinnedProtocol: modelData.protocol
                }
              }
            }
          }

          Column {
            visible: root.showMain && root.tab === "locations"
            width: parent.width
            spacing: Style.space(6)

            FoldHeader {
              section: "locationsHeader"
              title: root.filterText !== "" ? "MATCHING" : "LOCATIONS"
              count: amnezia.state.account.known ? root.filteredCountries.length : -1
              expanded: root.locationsExpanded
              onToggled: root.locationsExpanded = !root.locationsExpanded
            }

            TextField {
              id: filterField
              visible: root.locationsExpanded && amnezia.countries.length > 0
              width: parent.width
              foreground: root.foreground
              placeholderText: "Filter locations — press / to search"
              onTextChanged: root.filterText = text
              Keys.onEscapePressed: function(event) {
                text = ""
                keyCatcher.forceActiveFocus()
                event.accepted = true
              }
              Keys.onReturnPressed: function(event) {
                keyCatcher.forceActiveFocus()
                if (root.filteredCountries.length > 0) {
                  root.keyboardMoved = true
                  root.setCursor("locations", 0)
                }
                event.accepted = true
              }
              Keys.onDownPressed: function(event) {
                keyCatcher.forceActiveFocus()
                if (root.filteredCountries.length > 0) {
                  root.keyboardMoved = true
                  root.setCursor("locations", 0)
                }
                event.accepted = true
              }
            }

            Text {
              visible: root.locationsExpanded && root.filteredCountries.length === 0
              width: parent.width
              text: {
                if (amnezia.accountRefreshing) return "Asking Amnezia for your locations…"
                if (amnezia.countries.length === 0) return "No locations yet — press r to ask Amnezia."
                return "Nothing matches “" + root.filterText + "”."
              }
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }

            Column {
              id: locationColumn
              visible: root.locationsExpanded
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: root.filteredCountries
                LocationRow {
                  required property var modelData
                  required property int index
                  width: locationColumn.width
                  section: "locations"
                  rowIndex: index
                  entry: modelData
                }
              }
            }
          }

          // ── Account ──────────────────────────────────────────────────
          Column {
            visible: root.showMain && root.tab === "account"
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "SUBSCRIPTION"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              width: parent.width
              spacing: Style.spacing.labelGap

              InfoPair { label: "Plan"; value: amnezia.state.plan !== "" ? amnezia.state.plan : "Amnezia Premium" }
              InfoPair {
                visible: amnezia.state.account.endDate !== ""
                label: "Valid until"
                value: Model.validUntil(amnezia.state)
                valueColor: amnezia.state.daysLeft !== null && amnezia.state.daysLeft < 7 ? root.urgent : root.foreground
              }
              InfoPair {
                visible: amnezia.state.account.maxDevices > 0
                label: "Devices"
                value: amnezia.state.account.activeDevices + " of " + amnezia.state.account.maxDevices
              }
              InfoPair {
                visible: amnezia.state.account.support.email !== undefined
                label: "Support"
                value: amnezia.state.account.support.email || ""
              }
              InfoPair {
                visible: amnezia.state.account.support.telegram !== undefined
                label: "Telegram"
                value: amnezia.state.account.support.telegram || ""
              }
            }

            Column {
              id: accountColumn
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: root.accountActions
                ActionRow {
                  required property var modelData
                  required property int index
                  width: accountColumn.width
                  section: "account"
                  rowIndex: index
                  icon: modelData.icon
                  title: modelData.title
                  subtitle: modelData.key === "refresh" ? root.checkedText : modelData.detail
                  enabled: !amnezia.working
                  onClicked: root.runAccountAction(modelData.key)
                }
              }
            }

            FoldHeader {
              visible: amnezia.devices.length > 0
              section: "devicesHeader"
              title: "DEVICES"
              count: amnezia.devices.length
              expanded: root.devicesExpanded
              onToggled: root.devicesExpanded = !root.devicesExpanded
            }

            Column {
              id: deviceColumn
              visible: root.devicesExpanded && amnezia.devices.length > 0
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: amnezia.devices
                ActionRow {
                  required property var modelData
                  required property int index
                  width: deviceColumn.width
                  section: "devices"
                  rowIndex: index
                  icon: modelData.me || /linux|arch|ubuntu|debian|fedora|windows|mac/i.test(modelData.os)
                    ? root.laptopGlyph : root.phoneGlyph
                  iconColor: modelData.me ? root.foreground : root.dim
                  title: Model.deviceName(modelData)
                  subtitle: Model.deviceDetail(modelData, root.nowMs)
                  trailing: modelData.uuid !== "" ? "FREE SLOT" : ""
                  enabled: !amnezia.working && modelData.uuid !== ""
                  onClicked: root.askRevoke(modelData)
                }
              }
            }
          }
        }
      }
    }
  }

  // ── Components ─────────────────────────────────────────────────────────

  component TabPill: Button {
    property string tabKey: ""
    fontSize: Style.font.bodySmall
    foreground: root.foreground
    fontFamily: root.fontFamily
    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
    bordered: true
    active: root.tab === tabKey
    hasCursor: root.at("tabs") && root.tab === tabKey
    onHovered: function(isHovered) { if (isHovered) root.setCursor("tabs", 0) }
    onClicked: root.setTab(tabKey)
  }

  component FoldHeader: CursorSurface {
    id: foldHeader
    property string section: ""
    property string title: ""
    property int count: -1
    property bool expanded: true
    signal toggled()

    x: -Style.space(10)
    width: parent.width + Style.space(20)
    hasCursor: root.at(section, 0)
    foreground: root.foreground
    implicitHeight: foldLabel.implicitHeight + Style.space(10)
    onHasCursorChanged: if (hasCursor && root.keyboardMoved) root.scrollIntoView(foldHeader)

    MouseArea {
      id: foldMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onPositionChanged: function(mouse) { root.hoverCursor(foldMouse, mouse, foldHeader.section, 0) }
      onClicked: foldHeader.toggled()
    }

    PanelSectionHeader {
      id: foldLabel
      anchors.left: parent.left
      anchors.leftMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      text: foldHeader.title
      foreground: root.foreground
      fontFamily: root.fontFamily
    }

    Text {
      anchors.left: foldLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: foldLabel.verticalCenter
      visible: foldHeader.count >= 0
      text: String(foldHeader.count)
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      anchors.right: parent.right
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: foldLabel.verticalCenter
      text: foldHeader.expanded ? root.chevronOpen : root.chevronClosed
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }
  }

  component LocationRow: CursorSurface {
    id: locationRow
    property var entry: null
    property string section: "locations"
    property int rowIndex: 0
    // Recent rows remember the protocol they were used with.
    property string pinnedProtocol: ""

    readonly property string code: entry ? entry.code : ""
    readonly property string wanted: pinnedProtocol !== "" ? pinnedProtocol : amnezia.protocol
    readonly property string useProtocol: entry ? Model.protocolFor(entry, wanted) : wanted
    readonly property bool isLive: code !== "" && amnezia.liveCountry === code && !amnezia.working
    readonly property bool isPending: code !== "" && amnezia.working && amnezia.pendingCountry === code
    readonly property bool isPicked: code !== "" && amnezia.state.picked.country === code
    readonly property bool lacksProtocol: entry !== null && !Model.offers(entry, wanted)

    hasCursor: root.at(section, rowIndex)
    current: isLive
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX
    onHasCursorChanged: if (hasCursor && root.keyboardMoved) root.scrollIntoView(locationRow)

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onPositionChanged: function(mouse) { root.hoverCursor(rowMouse, mouse, locationRow.section, locationRow.rowIndex) }
      onClicked: if (locationRow.entry) amnezia.connectTo(locationRow.code, locationRow.useProtocol)
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      Text {
        text: locationRow.entry && locationRow.entry.flag !== "" ? locationRow.entry.flag : root.pinGlyph
        color: root.foreground
        opacity: locationRow.lacksProtocol ? 0.5 : 1.0
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rowContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          Layout.fillWidth: true
          text: locationRow.entry ? locationRow.entry.name : ""
          textFormat: Text.PlainText
          color: locationRow.lacksProtocol ? root.dim : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          Layout.fillWidth: true
          text: {
            if (!locationRow.entry) return ""
            if (locationRow.isPending) return "Connecting over " + Model.protocolLabel(locationRow.useProtocol) + "…"
            if (locationRow.isLive) {
              var up = Model.uptime(amnezia.state, root.nowMs)
              return "Connected · " + Model.protocolLabel(amnezia.state.protocol) + (up !== "" ? " · " + up : "")
            }
            if (locationRow.section === "recent") return Model.protocolLabel(locationRow.useProtocol)
            var line = Model.protocolsLine(locationRow.entry)
            if (locationRow.lacksProtocol) line += " — no " + Model.protocolLabel(locationRow.wanted)
            return (locationRow.isPicked && !amnezia.state.connected ? "Next · " : "") + line
          }
          textFormat: Text.PlainText
          color: locationRow.isLive ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Text {
        visible: locationRow.isLive || locationRow.isPending
        text: locationRow.isPending ? root.loadingGlyph : root.checkGlyph
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }

  component ActionRow: CursorSurface {
    id: actionRow
    property string section: ""
    property int rowIndex: 0
    property string icon: ""
    property color iconColor: root.foreground
    property string title: ""
    property string subtitle: ""
    property string trailing: ""
    signal clicked()

    hasCursor: root.at(section, rowIndex)
    foreground: root.foreground
    implicitHeight: actionContent.implicitHeight + Style.spacing.rowPaddingX
    opacity: enabled ? 1.0 : 0.6
    onHasCursorChanged: if (hasCursor && root.keyboardMoved) root.scrollIntoView(actionRow)

    MouseArea {
      id: actionMouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: actionRow.enabled
      cursorShape: Qt.PointingHandCursor
      onPositionChanged: function(mouse) { root.hoverCursor(actionMouse, mouse, actionRow.section, actionRow.rowIndex) }
      onClicked: actionRow.clicked()
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      Text {
        visible: actionRow.icon !== ""
        text: actionRow.icon
        color: actionRow.iconColor
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: actionContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          Layout.fillWidth: true
          text: actionRow.title
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          Layout.fillWidth: true
          visible: actionRow.subtitle !== ""
          text: actionRow.subtitle
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Text {
        visible: actionRow.trailing !== ""
        text: actionRow.trailing
        textFormat: Text.PlainText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        Layout.alignment: Qt.AlignVCenter
      }
    }
  }

  component InfoPair: Item {
    property string label: ""
    property string value: ""
    property color valueColor: root.foreground

    width: parent ? parent.width : 0
    implicitHeight: Math.max(pairLabel.implicitHeight, pairValue.implicitHeight)

    Text {
      id: pairLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      text: parent.label
      color: root.foreground
      opacity: 0.6
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      id: pairValue
      anchors.right: parent.right
      anchors.left: pairLabel.right
      anchors.leftMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      horizontalAlignment: Text.AlignRight
      text: parent.value
      textFormat: Text.PlainText
      color: parent.valueColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }
}
