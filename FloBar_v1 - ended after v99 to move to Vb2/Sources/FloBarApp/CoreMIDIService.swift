import CoreMIDI
import Foundation
import FloBarCore

private final class MIDIEndpointContext {
    let name: String
    init(_ name: String) { self.name = name }
}

enum MIDIOutputOrigin: Equatable { case startupSync, localButton, controllerAction }

final class CoreMIDIService {
    var onEvent: ((MIDIEvent, String) -> Void)?
    var onEndpointsChanged: (([String], [String]) -> Void)?
    var onOutputSent: ((MIDIEvent, MIDIOutputOrigin) -> Void)?
    private let queue = DispatchQueue(label: "FloBar.CoreMIDI", qos: .userInitiated)
    private var client = MIDIClientRef()
    private var inputPort = MIDIPortRef()
    private var outputPort = MIDIPortRef()
    private var bus1: MIDIEndpointRef = 0
    private var connected: [(MIDIEndpointRef, UnsafeMutableRawPointer)] = []
    private var running = false
    private var startupSyncSent = false

    func start() {
        queue.async { [weak self] in
            guard let self, !self.running else { return }
            self.running = true
            let clientStatus = MIDIClientCreateWithBlock("FloBar" as CFString, &self.client) { [weak self] _ in
                self?.queue.async { self?.reconnectEndpoints() }
            }
            guard clientStatus == noErr else { self.running = false; return }
            guard MIDIInputPortCreateWithBlock(self.client, "FloBar Input" as CFString, &self.inputPort, { [weak self] packetList, sourceRef in
                guard let self else { return }
                let source = sourceRef.map { Unmanaged<MIDIEndpointContext>.fromOpaque($0).takeUnretainedValue().name } ?? "Unknown MIDI"
                self.consume(packetList, source: source)
            }) == noErr else { self.running = false; return }
            guard MIDIOutputPortCreate(self.client, "FloBar Output" as CFString, &self.outputPort) == noErr else { self.running = false; return }
            self.reconnectEndpoints()
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self else { return }
            self.running = false
            self.disconnectAll()
            if self.inputPort != 0 { MIDIPortDispose(self.inputPort); self.inputPort = 0 }
            if self.outputPort != 0 { MIDIPortDispose(self.outputPort); self.outputPort = 0 }
            if self.client != 0 { MIDIClientDispose(self.client); self.client = 0 }
        }
    }

    func sendCC(channel: Int, number: Int, value: Int, origin: MIDIOutputOrigin = .localButton) {
        let safeNumber = max(0, min(127, number)); let safeValue = max(0, min(127, value))
        send(bytes: [UInt8(0xB0 | max(0, min(15, channel - 1))), UInt8(safeNumber), UInt8(safeValue)], event: .cc(channel: channel, number: safeNumber, value: safeValue), origin: origin)
    }

    func sendNote(channel: Int, number: Int, velocity: Int, origin: MIDIOutputOrigin = .localButton) {
        let safeNumber = max(0, min(127, number)); let safeVelocity = max(0, min(127, velocity))
        send(bytes: [UInt8(0x90 | max(0, min(15, channel - 1))), UInt8(safeNumber), UInt8(safeVelocity)], event: .noteOn(channel: channel, number: safeNumber, velocity: safeVelocity), origin: origin)
    }

    private func send(bytes: [UInt8], event: MIDIEvent, origin: MIDIOutputOrigin) {
        queue.async { [weak self] in
            guard let self, self.outputPort != 0, self.bus1 != 0, bytes.count <= 3 else { return }
            let capacity = 1024
            let storage = UnsafeMutableRawPointer.allocate(byteCount: capacity, alignment: MemoryLayout<MIDIPacketList>.alignment)
            defer { storage.deallocate() }
            let list = storage.assumingMemoryBound(to: MIDIPacketList.self)
            let first = MIDIPacketListInit(list)
            let timestamp = MIDITimeStamp(0)
            _ = bytes.withUnsafeBufferPointer { buffer in
                MIDIPacketListAdd(list, capacity, first, timestamp, buffer.count, buffer.baseAddress!)
            }
            guard MIDISend(self.outputPort, self.bus1, list) == noErr else { return }
            if origin != .startupSync { self.onOutputSent?(event, origin) }
        }
    }

    func sendStartupSyncOnce() {
        queue.async { [weak self] in
            guard let self, !self.startupSyncSent else { return }
            guard self.outputPort != 0, self.bus1 != 0 else { return }
            self.startupSyncSent = true
            for (index, item) in StartupSyncEvent.approvedSequence.enumerated() {
                self.queue.asyncAfter(deadline: .now() + .milliseconds(index * 150)) { [weak self] in
                    guard let self, self.running, self.bus1 != 0 else { return }
                    if item.kind == .noteOn { self.sendNoteNow(channel: item.channel, number: item.number, velocity: item.value) }
                    else { self.sendCCNow(channel: item.channel, number: item.number, value: item.value) }
                }
            }
        }
    }

    private func sendCCNow(channel: Int, number: Int, value: Int) {
        sendNow(bytes: [UInt8(0xB0 | max(0, min(15, channel - 1))), UInt8(number), UInt8(value)])
    }

    private func sendNoteNow(channel: Int, number: Int, velocity: Int) {
        sendNow(bytes: [UInt8(0x90 | max(0, min(15, channel - 1))), UInt8(number), UInt8(velocity)])
    }

    private func sendNow(bytes: [UInt8]) {
        guard outputPort != 0, bus1 != 0 else { return }
        let capacity = 256
        let storage = UnsafeMutableRawPointer.allocate(byteCount: capacity, alignment: MemoryLayout<MIDIPacketList>.alignment)
        defer { storage.deallocate() }
        let list = storage.assumingMemoryBound(to: MIDIPacketList.self)
        let packet = MIDIPacketListInit(list)
        _ = bytes.withUnsafeBufferPointer { MIDIPacketListAdd(list, capacity, packet, 0, $0.count, $0.baseAddress!) }
        _ = MIDISend(outputPort, bus1, list)
    }

    private func reconnectEndpoints() {
        guard running else { return }
        disconnectAll()
        var inputs: [String] = []
        var outputs: [String] = []
        let sourceCount = MIDIGetNumberOfSources()
        if sourceCount > 0 {
            for index in 0..<sourceCount {
                let endpoint = MIDIGetSource(index)
                let name = Self.name(of: endpoint)
                guard endpoint != 0 else { continue }
                inputs.append(name)
                let context = Unmanaged.passRetained(MIDIEndpointContext(name)).toOpaque()
                if MIDIPortConnectSource(inputPort, endpoint, context) == noErr { connected.append((endpoint, context)) }
                else { Unmanaged<MIDIEndpointContext>.fromOpaque(context).release() }
            }
        }
        let destinationCount = MIDIGetNumberOfDestinations()
        if destinationCount > 0 {
            for index in 0..<destinationCount {
                let endpoint = MIDIGetDestination(index)
                let name = Self.name(of: endpoint)
                if endpoint != 0 { outputs.append(name) }
                if name.localizedCaseInsensitiveContains("IAC Driver Bus 1") { bus1 = endpoint }
            }
        }
        onEndpointsChanged?(inputs, outputs)
    }

    private func disconnectAll() {
        for (endpoint, context) in connected {
            _ = MIDIPortDisconnectSource(inputPort, endpoint)
            Unmanaged<MIDIEndpointContext>.fromOpaque(context).release()
        }
        connected.removeAll()
        bus1 = 0
    }

    private static func name(of endpoint: MIDIEndpointRef) -> String {
        guard endpoint != 0 else { return "" }
        var unmanaged: Unmanaged<CFString>?
        let status = MIDIObjectGetStringProperty(endpoint, kMIDIPropertyDisplayName, &unmanaged)
        if status == noErr, let value = unmanaged?.takeRetainedValue() { return value as String }
        return "MIDI Endpoint"
    }

    private func consume(_ packetList: UnsafePointer<MIDIPacketList>, source: String) {
        let mutableList = UnsafeMutablePointer(mutating: packetList)
        let packetCount = Int(mutableList.pointee.numPackets)
        guard let packetOffset = MemoryLayout<MIDIPacketList>.offset(of: \.packet) else { return }
        var packetPointer = UnsafeMutablePointer(mutating: UnsafeRawPointer(packetList)
            .advanced(by: packetOffset).assumingMemoryBound(to: MIDIPacket.self))
        for _ in 0..<packetCount {
            let packet = packetPointer.pointee
            let count = Int(packet.length)
            let data = withUnsafeBytes(of: packet.data) { Array($0.prefix(count)) }
            parse(data, source: source)
            packetPointer = MIDIPacketNext(packetPointer)
        }
    }

    private func parse(_ data: [UInt8], source: String) {
        var index = 0
        while index < data.count {
            let status = data[index]
            if status < 0x80 { index += 1; continue }
            let type = status & 0xF0
            let channel = Int(status & 0x0F) + 1
            let needed = (type == 0xC0 || type == 0xD0) ? 2 : 3
            guard index + needed <= data.count else { return }
            if type == 0xB0 { onEvent?(.cc(channel: channel, number: Int(data[index + 1]), value: Int(data[index + 2])), source) }
            else if type == 0x90 {
                let velocity = Int(data[index + 2])
                if velocity == 0 { onEvent?(.noteOff(channel: channel, number: Int(data[index + 1])), source) }
                else { onEvent?(.noteOn(channel: channel, number: Int(data[index + 1]), velocity: velocity), source) }
            } else if type == 0x80 { onEvent?(.noteOff(channel: channel, number: Int(data[index + 1])), source) }
            index += needed
        }
    }
}
