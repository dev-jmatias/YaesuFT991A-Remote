// Radio Remote PTT bridge for macOS.
//
// Chrome on a Mac cannot read the PTT button of a Bluetooth speaker-mic that sends it over the Serial Port Profile
// (RFCOMM), for example "+PTT=P" when pressed and "+PTT=R" when released. This small program can: it keeps the
// connection to the paired device and passes the presses to the Radio Remote page over a local WebSocket.
//
//   ws://127.0.0.1:17991   messages are JSON text, one way only (helper -> page), nothing a page sends is acted on:
//     {"t":"hello","v":1,"connected":false,"device":""}   sent on connect, and whenever the device connects or goes away
//     {"t":"ptt","down":true}  /  {"t":"ptt","down":false}
//
// Build:  swiftc -O -o ptt-bridge ptt-bridge.swift
// Run:    ./ptt-bridge   (as Radio Remote PTT.app it lives in the menu bar; --cli forces the text mode)
//          ./ptt-bridge            (--list shows the paired devices, --name TEXT only uses devices whose name contains TEXT,
//                                  --port N changes the port, --simulate needs no Bluetooth: type p / r / x / c + Enter)
import Foundation
import IOBluetooth
import Network
import AppKit
import ServiceManagement

let hubVersion = 1
let gui = Bundle.main.bundlePath.hasSuffix(".app") && !CommandLine.arguments.contains("--cli")          // started as the app: a menu bar item, no text
var wsPort: UInt16 = 17991
var nameFilter: String? = nil
var simulate = false, listOnly = false
do {
    var a = Array(CommandLine.arguments.dropFirst())
    while !a.isEmpty {
        let k = a.removeFirst()
        switch k {
        case "--port": if let v = a.first, let n = UInt16(v) { wsPort = n; a.removeFirst() }
        case "--name": if let v = a.first { nameFilter = v.lowercased(); a.removeFirst() }
        case "--simulate": simulate = true
        case "--cli": break
        case "--list": listOnly = true
        default: print("Unknown option \(k). Options: --list, --name TEXT, --port N, --simulate"); exit(2)
        }
    }
}

func stamp() -> String { let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f.string(from: Date()) }
func log(_ s: String) { if !gui { print(stamp(), s); fflush(stdout) } }

// ---- WebSocket side ----
final class Hub {
    var conns: [ObjectIdentifier: NWConnection] = [:]
    var connected = false, device = "", down = false
    var onChange: (() -> Void)?
    func json(_ o: [String: Any]) -> Data { (try? JSONSerialization.data(withJSONObject: o)) ?? Data() }
    func send(_ c: NWConnection, _ o: [String: Any]) {
        let meta = NWProtocolWebSocket.Metadata(opcode: .text)
        let ctx = NWConnection.ContentContext(identifier: "msg", metadata: [meta])
        c.send(content: json(o), contentContext: ctx, isComplete: true, completion: .idempotent)
    }
    func broadcast(_ o: [String: Any]) { for c in conns.values { send(c, o) } }
    var helloMsg: [String: Any] { ["t": "hello", "v": hubVersion, "connected": connected, "device": device] }
    func setDevice(_ name: String?) {
        let now = name != nil
        if !now && down { down = false; broadcast(["t": "ptt", "down": false]) }          // never leave the radio keyed when the device goes away
        connected = now; device = name ?? ""
        broadcast(helloMsg); onChange?()
        log(now ? "device connected: \(device)" : "device gone")
    }
    func ptt(_ d: Bool) {
        if d == down { return }
        down = d; broadcast(["t": "ptt", "down": d]); log(d ? "PTT down" : "PTT up"); onChange?()
    }
    func add(_ c: NWConnection) {
        let id = ObjectIdentifier(c)
        conns[id] = c
        c.stateUpdateHandler = { [weak self, weak c] st in
            guard let self = self, let c = c else { return }
            switch st {
            case .ready: self.send(c, self.helloMsg); log("page connected (\(self.conns.count))")
            case .failed, .cancelled: if self.conns.removeValue(forKey: id) != nil { log("page left (\(self.conns.count))") }
            default: break
            }
        }
        func drain() { c.receiveMessage { _, _, _, err in if err == nil { drain() } } }          // incoming messages are ignored, but reading notices a close
        drain()
        c.start(queue: .main)
    }
}
let hub = Hub()

func startServer() {
    let ws = NWProtocolWebSocket.Options()
    ws.autoReplyPing = true
    let params = NWParameters(tls: nil)
    params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)
    params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: wsPort)!)       // this computer only
    do {
        let l = try NWListener(using: params)
        l.newConnectionHandler = { hub.add($0) }
        l.stateUpdateHandler = { st in
            if case .failed(let e) = st { if gui { exit(0) }; log("cannot listen on 127.0.0.1:\(wsPort): \(e). Is another copy running?"); exit(1) }
            if case .ready = st { log("listening on ws://127.0.0.1:\(wsPort)") }
        }
        l.start(queue: .main)
    } catch { log("cannot listen on port \(wsPort): \(error)"); exit(1) }
}

// ---- token parser: the stream has no separators, messages can arrive split, repeated or glued together ----
struct Tokens {
    var buf = ""
    mutating func feed(_ d: Data) -> [Bool] {
        buf += String(decoding: d.map { $0 < 128 ? $0 : 46 }, as: UTF8.self)
        var out: [Bool] = []
        while true {
            let p = buf.range(of: "+PTT=P"), r = buf.range(of: "+PTT=R")
            guard let hit = [p, r].compactMap({ $0 }).min(by: { $0.lowerBound < $1.lowerBound }) else { break }
            out.append(hit == p); buf = String(buf[hit.upperBound...])
        }
        if buf.count > 16 { buf = String(buf.suffix(8)) }
        return out
    }
}

// ---- Bluetooth side ----
func uuid16(_ u: IOBluetoothSDPUUID) -> UInt16? {
    let b = [UInt8](u as Data), n = b.count
    guard n == 2 || n == 4 || n == 16 else { return nil }
    let o = n == 2 ? 0 : 2                                                  // 4 and 16 byte forms keep the short id in bytes 2-3
    return UInt16(b[o]) << 8 | UInt16(b[o + 1])
}
// The RFCOMM channel is the number after the RFCOMM entry (0x0003) of the protocol list (attribute 0x0004) of the Serial Port (0x1101) record.
func sppChannels(_ rec: IOBluetoothSDPServiceRecord) -> [UInt8] {
    var found: [UInt8] = []
    if let top = rec.getAttributeDataElement(0x0004)?.getArrayValue() as? [IOBluetoothSDPDataElement] {
        for e in top {
            guard let items = e.getArrayValue() as? [IOBluetoothSDPDataElement], items.count >= 2,
                  let u = items[0].getUUIDValue(), uuid16(u) == 0x0003, let n = items[1].getNumberValue() else { continue }
            found.append(n.uint8Value)
        }
    }
    var ch: BluetoothRFCOMMChannelID = 0
    if rec.getRFCOMMChannelID(&ch) == kIOReturnSuccess, !found.contains(ch) { found.append(ch) }
    return found
}

// the record's service class list (attribute 0x0001) names the Serial Port Profile (0x1101); other records can use RFCOMM too (vendor services) and are left alone
func isSPP(_ rec: IOBluetoothSDPServiceRecord) -> Bool {
    guard let e = rec.getAttributeDataElement(0x0001) else { return false }
    let items = (e.getArrayValue() as? [IOBluetoothSDPDataElement]) ?? [e]
    return items.contains { $0.getUUIDValue().flatMap(uuid16) == 0x1101 }
}

final class Link: NSObject, IOBluetoothRFCOMMChannelDelegate {
    let dev: IOBluetoothDevice
    var chan: IOBluetoothRFCOMMChannel?
    var busy = false, isOpen = false, noSPP = false, nextTry = Date.distantPast
    var channels: [UInt8] = [], tokens = Tokens()
    var name: String { dev.name ?? dev.addressString ?? "?" }
    init(_ d: IOBluetoothDevice) { dev = d }

    func poll() {
        if isOpen || busy { return }
        if !dev.isConnected() {
            noSPP = false
            if nameFilter != nil, Date() >= nextTry { nextTry = Date().addingTimeInterval(15); dev.openConnection() }      // only a device the person named is woken up
            return
        }
        if noSPP || Date() < nextTry { return }
        busy = true
        log("\(name): looking up its services")
        dev.performSDPQuery(self)
    }
    @objc func sdpQueryComplete(_ device: IOBluetoothDevice!, status: IOReturn) {
        var chans: [UInt8] = []
        for rec in (dev.services as? [IOBluetoothSDPServiceRecord]) ?? [] where isSPP(rec) { chans += sppChannels(rec) }
        channels = chans
        log("\(name): serial channel(s) from the service records: \(chans)")
        if chans.isEmpty { busy = false; noSPP = status == 0; nextTry = Date().addingTimeInterval(30); return }
        openNext()
    }
    func openNext() {
        guard let id = channels.first else { busy = false; nextTry = Date().addingTimeInterval(5); return }
        channels.removeFirst()
        var ch: IOBluetoothRFCOMMChannel? = nil
        let r = dev.openRFCOMMChannelAsync(&ch, withChannelID: id, delegate: self)
        if r != kIOReturnSuccess { log("\(name): channel \(id) open request failed (0x\(String(r, radix: 16)))"); openNext(); return }
        chan = ch
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in                  // an open that never answers must not block the next try
            if let s = self, s.busy, !s.isOpen { s.chan?.close(); s.chan = nil; s.openNext() }
        }
    }
    func rfcommChannelOpenComplete(_ c: IOBluetoothRFCOMMChannel!, status error: IOReturn) {
        if error == kIOReturnSuccess {
            busy = false; isOpen = true; tokens = Tokens()
            log("\(name): serial channel \(c.getID()) open")
            hub.setDevice(name)
        } else {
            log("\(name): channel \(c.getID()) refused (0x\(String(error, radix: 16)))")
            chan = nil; openNext()
        }
    }
    func rfcommChannelData(_ c: IOBluetoothRFCOMMChannel!, data ptr: UnsafeMutableRawPointer!, length: Int) {
        for d in tokens.feed(Data(bytes: ptr, count: length)) { hub.ptt(d) }
    }
    func rfcommChannelClosed(_ c: IOBluetoothRFCOMMChannel!) {
        if isOpen { log("\(name): serial channel closed"); hub.setDevice(nil) }
        isOpen = false; busy = false; chan = nil; nextTry = Date().addingTimeInterval(3)
    }
}

var links: [String: Link] = [:]
func scan() {
    for case let d as IOBluetoothDevice in IOBluetoothDevice.pairedDevices() ?? [] {
        let key = d.addressString ?? ""
        if let f = nameFilter, !(d.name ?? "").lowercased().contains(f) { continue }
        if links[key] == nil { links[key] = Link(d) }
        links[key]!.poll()
    }
}

// ---- main ----
if listOnly {
    for case let d as IOBluetoothDevice in IOBluetoothDevice.pairedDevices() ?? [] {
        print("\(d.name ?? "?")  \(d.addressString ?? "")  \(d.isConnected() ? "connected" : "not connected")")
    }
    exit(0)
}
startServer()
if simulate {
    log("simulation: type p (press), r (release), c (device connects), x (device goes away), then Enter")
    hub.setDevice("Simulated button")
    Thread.detachNewThread {
        while let line = readLine() {
            DispatchQueue.main.async {
                switch line.trimmingCharacters(in: .whitespaces) {
                case "p": hub.ptt(true)
                case "r": hub.ptt(false)
                case "c": hub.setDevice("Simulated button")
                case "x": hub.setDevice(nil)
                default: break
                }
            }
        }
    }
} else {
    log("looking for a paired Bluetooth serial device (\(nameFilter.map { "name contains \"\($0)\"" } ?? "any paired device")). Ctrl+C stops.")
    scan()
    Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in scan() }
}

// ---- menu bar item (app mode) ----
final class Bar: NSObject {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let state = NSMenuItem(title: "", action: nil, keyEquivalent: ""), login = NSMenuItem(title: "Start at login", action: nil, keyEquivalent: "")
    override init() {
        super.init()
        let m = NSMenu()
        state.isEnabled = false; m.addItem(state)
        m.addItem(.separator())
        login.target = self; login.action = #selector(toggleLogin); m.addItem(login)
        let q = NSMenuItem(title: "Quit Radio Remote PTT", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); m.addItem(q)
        item.menu = m
        refresh()
        hub.onChange = { [weak self] in self?.refresh() }
    }
    func refresh() {
        let sym = hub.down ? "mic.fill" : hub.connected ? "mic" : "mic.slash"
        if let img = NSImage(systemSymbolName: sym, accessibilityDescription: "PTT") { img.isTemplate = true; item.button?.image = img; item.button?.title = "" }
        else { item.button?.title = hub.down ? "TX" : hub.connected ? "PTT" : "PTT?" }
        state.title = hub.down ? "Button pressed" : hub.connected ? "Connected: \(hub.device)" : "No device connected (pair it in Bluetooth settings, switch it on)"
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }
    @objc func toggleLogin() {
        do { if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() } } catch { /* shown by the check mark staying as it was */ }
        refresh()
    }
}
if gui {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let bar = Bar(); _ = bar
    app.run()
}
RunLoop.main.run()
