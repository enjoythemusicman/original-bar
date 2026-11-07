import CoreFoundation
import Foundation
import FloBarCore

/// Low-frequency, best-effort macOS Bluetooth snapshot. A percentage is exposed only
/// when the report contains a value associated with the matching device name.
final class BluetoothBatteryMonitor {
    var onSnapshot: (([ControllerKind: Bool], [ControllerKind: Int]) -> Void)?
    private let queue = DispatchQueue(label: "FloBar.BluetoothBattery", qos: .utility)
    private var timer: DispatchSourceTimer?
    private var scanInProgress = false

    func start() {
        queue.async { [weak self] in
            guard let self, self.timer == nil else { return }
            self.scan()
            let timer = DispatchSource.makeTimerSource(queue: self.queue)
            timer.schedule(deadline: .now() + 900, repeating: 900, leeway: .seconds(10))
            timer.setEventHandler { [weak self] in self?.scan() }
            self.timer = timer
            timer.resume()
        }
    }

    func stop() {
        queue.async { [weak self] in self?.timer?.cancel(); self?.timer = nil }
    }

    private func scan() {
        guard !scanInProgress else { return }
        scanInProgress = true
        defer { scanInProgress = false }
        guard let report = run("/usr/sbin/system_profiler", arguments: ["-json", "SPBluetoothDataType"], timeout: 30),
              let root = try? JSONSerialization.jsonObject(with: report) else { return }
        let parsed = parse(root)
        var levels: [ControllerKind: Int] = [:]
        var connected = parsed.connected
        if let smc = run("/usr/bin/pmset", arguments: ["-g", "accps"], timeout: 5),
           let text = String(data: smc, encoding: .utf8),
           let value = Self.smcBattery(in: text) {
            levels[.smc] = value
            connected[.smc] = true
        }
        if let value = parsed.levels[.p23] { levels[.p23] = value }
        onSnapshot?(connected, levels)
    }

    private func run(_ path: String, arguments: [String], timeout: TimeInterval) -> Data? {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout) {
            if process.isRunning { process.terminate() }
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        return data
    }

    private func parse(_ root: Any) -> (connected: [ControllerKind: Bool], levels: [ControllerKind: Int]) {
        let aliases: [ControllerKind: [String]] = [
            .smc: ["smc-mixer", "smc mixer"],
            .p23: ["p23", "pt23", "pt 23", "p-23", "airpods p23"]
        ]
        var connected: [ControllerKind: Bool] = [:]
        var levels: [ControllerKind: Int] = [:]
        let batteryPattern = try? NSRegularExpression(pattern: "battery|charge|power[_ ]?level|powerlevel|batterypercent|batterypercentage", options: [.caseInsensitive])

        func numbers(in value: Any) -> [Int] {
            if let number = value as? NSNumber {
                guard CFGetTypeID(number) != CFBooleanGetTypeID() else { return [] }
                let intValue = number.intValue
                return number.doubleValue == Double(intValue) && (0...100).contains(intValue) ? [intValue] : []
            }
            if let text = value as? String, let range = text.range(of: #"^\s*(\d{1,3})\s*%?\s*$"#, options: .regularExpression),
               let number = Int(text[range].filter(\.isNumber)), (0...100).contains(number) { return [number] }
            if let dictionary = value as? [String: Any] { return dictionary.values.flatMap(numbers) }
            if let array = value as? [Any] { return array.flatMap(numbers) }
            return []
        }

        func visit(_ node: Any, inherited: Set<ControllerKind> = [], batteryContext: Bool = false) {
            if let dictionary = node as? [String: Any] {
                let identity = (dictionary.keys.map { $0 } + dictionary.values.compactMap { $0 as? String }).joined(separator: " ").lowercased()
                var matched = inherited
                for (kind, names) in aliases where names.contains(where: { identity.contains($0) }) { matched.insert(kind) }
                for kind in matched where connected[kind] == nil { connected[kind] = true }
                for (key, value) in dictionary {
                    let isBattery = batteryContext || batteryPattern?.firstMatch(in: key, range: NSRange(key.startIndex..., in: key)) != nil
                    if isBattery {
                        for kind in matched where levels[kind] == nil {
                            if let number = numbers(in: value).first { levels[kind] = number }
                        }
                    }
                    if key.localizedCaseInsensitiveContains("connected") {
                        let normalized = String(describing: value).lowercased()
                        if normalized == "no" || normalized == "false" || normalized == "0" {
                            for kind in matched { connected[kind] = false }
                        } else if normalized == "yes" || normalized == "true" || normalized == "1" {
                            for kind in matched { connected[kind] = true }
                        }
                    }
                    visit(value, inherited: matched, batteryContext: batteryPattern?.firstMatch(in: key, range: NSRange(key.startIndex..., in: key)) != nil)
                }
            } else if let array = node as? [Any] {
                array.forEach { visit($0, inherited: inherited, batteryContext: batteryContext) }
            }
        }
        visit(root)
        return (connected, levels)
    }

    private static func smcBattery(in report: String) -> Int? {
        let pattern = try? NSRegularExpression(pattern: #"SMC-Mixer[^\n]*?(\d{1,3})\s*%"#, options: [.caseInsensitive])
        guard let match = pattern?.firstMatch(in: report, range: NSRange(report.startIndex..., in: report)),
              let range = Range(match.range(at: 1), in: report),
              let value = Int(report[range]), (0...100).contains(value) else { return nil }
        return value
    }
}
