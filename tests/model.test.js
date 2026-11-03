// Model.js under node: the QML-only first line is dropped and the functions
// are taken from the script's scope. Run: node tests/model.test.js [state.json]
"use strict"
const fs = require("fs")
const path = require("path")
const assert = require("assert")

const source = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8").replace(/^\.pragma library.*$/m, "")
const names = [...source.matchAll(/^function (\w+)/gm)].map(m => m[1])
const Model = new Function(source + "\nreturn {" + names.join(",") + ", protocols: protocols}")()

const now = Date.parse("2026-11-01T12:00:00Z")
const sample = process.argv[2] ? fs.readFileSync(process.argv[2], "utf8") : null

let passed = 0
function test(name, fn) {
  fn()
  passed++
  console.log("ok - " + name)
}

test("rejects junk and oversized answers", () => {
  assert.strictEqual(Model.parseState(""), null)
  assert.strictEqual(Model.parseState("not json"), null)
  assert.strictEqual(Model.parseState('{"ok":false}'), null)
  assert.strictEqual(Model.parseState("{" + " ".repeat(300000) + "}"), null)
})

test("parses and clamps a state", () => {
  const raw = JSON.stringify({
    ok: true, hasKey: true, connected: true, country: "de", protocol: "vless", iface: "amnvless",
    since: now / 1000 - 754, rx: 13002342, tx: 3250000, daysLeft: 3,
    picked: { country: "de", protocol: "vless" }, xray: true, awgBackend: "daemon",
    account: { known: true, endDate: "2027-03-01", activeDevices: 3, maxDevices: 5, support: { email: "a@b.c", evil: 1 } },
    countries: [{ code: "de", name: "Germany", flag: "🇩🇪", protocols: ["awg", "vless", "bogus"] },
                { code: "BAD!", name: "x" }, { code: "us-east", name: "USA", protocols: ["vless"] }],
    devices: [{ uuid: "5b0d2c55-3d7c-4f3c-9d8e-1a2b3c4d5e6f", os: "android", country_name: "Netherlands",
                source: "gateway_account", last: now / 1000 - 3 * 86400, me: false },
              { uuid: "not-a-uuid", os: "x".repeat(500), me: true }],
    recent: [{ country: "de", protocol: "awg" }, { country: "zz", protocol: "nope" }],
    configs: { "de-vless": { endpoint: "203.0.113.20:443", transport: "tcp/reality" } },
  })
  const s = Model.parseState(raw)
  assert.ok(s)
  assert.deepStrictEqual(s.countries.map(c => c.code), ["de", "us-east"])
  assert.deepStrictEqual(s.countries[0].protocols, ["awg", "vless"])
  assert.strictEqual(s.devices[1].uuid, "")
  assert.strictEqual(s.devices[1].os.length, 32)
  assert.deepStrictEqual(Object.keys(s.account.support), ["email"])
  assert.strictEqual(s.recent[1].protocol, "awg")
  assert.strictEqual(s.iface, "amnvless")

  assert.strictEqual(Model.heroMeta(s, "", now), "🇩🇪 Germany · VLESS · 12m")
  assert.ok(Model.barTooltip(s, "", "", now).startsWith("Amnezia: 🇩🇪 Germany · VLESS\n↓ 12 MB"))
  assert.strictEqual(Model.validUntil(s), "2027-03-01 · 3 days")
  assert.strictEqual(Model.deviceName(s.devices[0]), "Android")
  assert.strictEqual(Model.deviceDetail(s.devices[0], now), "Netherlands · app · 3d ago")
  assert.strictEqual(Model.deviceName(s.devices[1]), "This computer")
  assert.strictEqual(Model.config(s, "de", "vless").transport, "tcp/reality")
  const rows = Model.recentRows(s.recent, s.countries)
  assert.strictEqual(rows.length, 1)
  assert.strictEqual(rows[0].entry.name, "Germany")
})

test("protocol choice follows what a location offers", () => {
  const usa = { code: "us-east", name: "USA", flag: "", protocols: ["vless"] }
  assert.strictEqual(Model.protocolFor(usa, "awg"), "vless")
  assert.strictEqual(Model.protocolFor(usa, "vless"), "vless")
  assert.ok(!Model.offers(usa, "awg"))
  assert.strictEqual(Model.protocolsLine({ protocols: ["awg", "vless"] }), "AmneziaWG · VLESS")
})

test("filtering matches names and code prefixes", () => {
  const list = [{ code: "de", name: "Germany" }, { code: "nl", name: "Netherlands" }, { code: "us-east", name: "USA (East)" }]
  assert.deepStrictEqual(Model.filterCountries(list, "ger").map(c => c.code), ["de"])
  assert.deepStrictEqual(Model.filterCountries(list, "US").map(c => c.code), ["us-east"])
  assert.strictEqual(Model.filterCountries(list, "").length, 3)
})

test("notes say what each protocol needs", () => {
  const base = Model.emptyState()
  assert.strictEqual(Model.protocolNote(base, "vless").level, "missing")
  assert.strictEqual(Model.protocolNote(Object.assign({}, base, { xray: true }), "vless").level, "warn")
  assert.strictEqual(Model.protocolNote(Object.assign({}, base, { awgBackend: "daemon" }), "awg").level, "ok")
  assert.strictEqual(Model.protocolNote(base, "awg").level, "missing")
  const quick = Object.assign({}, base, { awgBackend: "quick" })
  assert.strictEqual(Model.protocolNote(quick, "awg").level, "missing")
  assert.ok(Model.protocolNote(quick, "awg").text.indexOf("amneziawg-go") >= 0)
  assert.strictEqual(Model.protocolNote(Object.assign({}, quick, { awgModule: "available" }), "awg").level, "warn")
  assert.strictEqual(Model.protocolNote(Object.assign({}, quick, { awgGo: true }), "awg").level, "warn")
})

test("errors read like sentences", () => {
  assert.strictEqual(Model.commandError(1, "amnezia: no key yet\n", ""), "no key yet")
  assert.strictEqual(Model.commandError(1, "line one\namnezia: authorization cancelled", ""), "Authorization cancelled")
  assert.strictEqual(Model.commandError(3, "", ""), "Failed (exit 3)")
  assert.strictEqual(Model.formatBytes(0), "0 B")
  assert.strictEqual(Model.formatBytes(1536), "1.5 KB")
  assert.strictEqual(Model.formatDuration(3720), "1h 2m")
})

if (sample) {
  test("parses what `amnezia state` printed", () => {
    const s = Model.parseState(sample)
    assert.ok(s, "the CLI's state did not parse")
    assert.ok(s.countries.length > 0)
    assert.ok(typeof Model.heroMeta(s, "", now) === "string")
  })
}

console.log(passed + " passed")
