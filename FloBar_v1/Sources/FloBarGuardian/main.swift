import AppKit
import Darwin
import Foundation

@main
struct FloBarGuardianMain {
    static func main() {
        let arguments = CommandLine.arguments
        guard let appIndex = arguments.firstIndex(of: "--app"), arguments.indices.contains(appIndex + 1),
              let heartbeatIndex = arguments.firstIndex(of: "--heartbeat"), arguments.indices.contains(heartbeatIndex + 1),
              let recoveryIndex = arguments.firstIndex(of: "--recovery-marker"), arguments.indices.contains(recoveryIndex + 1),
              let commandIndex = arguments.firstIndex(of: "--command"), arguments.indices.contains(commandIndex + 1),
              let logIndex = arguments.firstIndex(of: "--log"), arguments.indices.contains(logIndex + 1) else { exit(64) }
        let guardian = Guardian(appURL: URL(fileURLWithPath: arguments[appIndex + 1]),
                                heartbeatURL: URL(fileURLWithPath: arguments[heartbeatIndex + 1]),
                                recoveryMarkerURL: URL(fileURLWithPath: arguments[recoveryIndex + 1]),
                                commandURL: URL(fileURLWithPath: arguments[commandIndex + 1]),
                                logURL: URL(fileURLWithPath: arguments[logIndex + 1]))
        guardian.run()
    }
}

private final class Guardian {
    private let appURL: URL
    private let heartbeatURL: URL
    private let recoveryMarkerURL: URL
    private let commandURL: URL
    private let logURL: URL
    private let pidURL: URL
    private let lockURL: URL
    private var restartTimes: [TimeInterval] = []
    private var lastAppPID: pid_t = 0
    private var sleeping = false
    private var wakeGraceUntil: TimeInterval = 0
    private var recoveryInProgress = false
    private var pendingExit: (pid: pid_t, manual: Bool, startedAt: TimeInterval)?
    private var awaitingNewHeartbeatSince: TimeInterval?
    private var timer: DispatchSourceTimer?
    private var lockDescriptor: Int32 = -1

    init(appURL: URL, heartbeatURL: URL, recoveryMarkerURL: URL, commandURL: URL, logURL: URL) {
        self.appURL = appURL; self.heartbeatURL = heartbeatURL; self.recoveryMarkerURL = recoveryMarkerURL
        self.commandURL = commandURL; self.logURL = logURL
        self.pidURL = heartbeatURL.deletingLastPathComponent().appendingPathComponent("guardian.pid")
        self.lockURL = heartbeatURL.deletingLastPathComponent().appendingPathComponent("guardian.lock")
    }

    func run() {
        guard acquireSingleInstanceLock() else { return }
        writePID()
        log("Guardian started; heartbeat interval 1s, hang threshold 5s, automatic restart limit 3 per 60s.")
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in self?.sleeping = true }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            guard let self else { return }
            self.sleeping = false; self.wakeGraceUntil = ProcessInfo.processInfo.systemUptime + 15
            self.log("Wake detected; heartbeat hang detection grace is up to 15s.")
        }
        let source = DispatchSource.makeTimerSource(queue: .main)
        source.schedule(deadline: .now() + 1, repeating: 1, leeway: .milliseconds(100))
        source.setEventHandler { [weak self] in
            self?.writePID()
            self?.tick()
        }
        timer = source; source.resume()
        RunLoop.main.run()
    }

    private func tick() {
        if let startedAt = awaitingNewHeartbeatSince {
            if let pid = heartbeatPID(), pid != lastAppPID, isTargetApp(pid) {
                lastAppPID = pid
                awaitingNewHeartbeatSince = nil
                recoveryInProgress = false
                try? FileManager.default.removeItem(at: recoveryMarkerURL)
                log("New FloBar process heartbeat confirmed (pid \(pid)).")
            } else if ProcessInfo.processInfo.systemUptime - startedAt >= 15 {
                log("New FloBar process did not confirm its heartbeat within 15s; stopping guardian retries.")
                finish()
            }
            return
        }
        if let pendingExit {
            if !isTargetApp(pendingExit.pid) {
                self.pendingExit = nil
                recoveryInProgress = false
                relaunch(manual: pendingExit.manual)
            } else if ProcessInfo.processInfo.systemUptime - pendingExit.startedAt >= 4 {
                _ = kill(pendingExit.pid, SIGKILL)
            }
            return
        }
        if let command = try? String(contentsOf: commandURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) {
            if command == "quit" {
                try? FileManager.default.removeItem(at: commandURL)
                log("Explicit Quit received; guardian is stopping without relaunch."); finish(); return
            }
            if command == "restart" {
                try? FileManager.default.removeItem(at: commandURL)
                log("Manual § restart requested; excluded from automatic restart limit.")
                let pid = heartbeatPID() ?? lastAppPID
                if pid > 0, isTargetApp(pid) { requestExit(pid: pid, manual: true) }
                else { relaunch(manual: true) }
                return
            }
        }
        guard !sleeping else { return }
        let values = (try? String(contentsOf: heartbeatURL, encoding: .utf8))?.split(whereSeparator: \.isNewline) ?? []
        guard values.count >= 2, let timestamp = Double(values[0]), let pid = Int32(values[1]) else {
            if ProcessInfo.processInfo.systemUptime > 20, !recoveryInProgress { relaunch(manual: false) }
            return
        }
        let appAge = ProcessInfo.processInfo.systemUptime - timestamp
        let appAlive = isTargetApp(pid)
        if !appAlive {
            if pid != lastAppPID { lastAppPID = pid; log("App process exit observed (pid \(pid)).") }
            if !recoveryInProgress { relaunch(manual: false) }
            return
        }
        lastAppPID = pid
        guard ProcessInfo.processInfo.systemUptime >= wakeGraceUntil else { return }
        if appAge >= 5 {
            log("UI heartbeat missed 5s; treating pid \(pid) as hung.")
            requestExit(pid: pid, manual: false)
        }
    }

    private func requestExit(pid: pid_t, manual: Bool) {
        guard !recoveryInProgress else { return }
        recoveryInProgress = true
        pendingExit = (pid, manual, ProcessInfo.processInfo.systemUptime)
        if !manual {
            try? "recovering\n".write(to: recoveryMarkerURL, atomically: true, encoding: .utf8)
        }
        _ = kill(pid, SIGTERM)
    }

    private func heartbeatPID() -> pid_t? {
        let values = (try? String(contentsOf: heartbeatURL, encoding: .utf8))?.split(whereSeparator: \.isNewline) ?? []
        guard values.count >= 2, let pid = Int32(values[1]) else { return nil }
        return pid
    }

    private func isTargetApp(_ pid: pid_t) -> Bool {
        guard pid > 0, kill(pid, 0) == 0 || errno == EPERM,
              let running = NSRunningApplication(processIdentifier: pid),
              running.bundleIdentifier == "com.galtalor.FloBar",
              let runningBundleURL = running.bundleURL else { return false }
        return runningBundleURL.standardizedFileURL.path == appURL.standardizedFileURL.path
    }

    private func relaunch(manual: Bool) {
        guard !recoveryInProgress else { return }
        recoveryInProgress = true
        let now = ProcessInfo.processInfo.systemUptime
        restartTimes.removeAll { now - $0 > 60 }
        if !manual {
            guard restartTimes.count < 3 else {
                log("Restart limit reached (3 in 60s); guardian will not retry until a manual start.")
                finish(); return
            }
            restartTimes.append(now)
        }
        guard launchApp() else { log("Guardian could not launch FloBar; stopping retries for this session."); finish(); return }
        log(manual ? "Manual restart launched." : "Automatic restart launched.")
        awaitingNewHeartbeatSince = ProcessInfo.processInfo.systemUptime
    }

    private func launchApp() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-n", "-a", appURL.path]
        do { try process.run(); process.waitUntilExit(); return process.terminationStatus == 0 }
        catch { log("Launch failed: \(error)"); return false }
    }

    private func acquireSingleInstanceLock() -> Bool {
        let parent = pidURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        lockDescriptor = open(lockURL.path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard lockDescriptor >= 0 else { return false }
        if flock(lockDescriptor, LOCK_EX | LOCK_NB) != 0 { close(lockDescriptor); lockDescriptor = -1; return false }
        return true
    }

    private func writePID() {
        try? "\(getpid())\n\(ProcessInfo.processInfo.systemUptime)\n".write(to: pidURL, atomically: true, encoding: .utf8)
    }

    private func finish() {
        timer?.cancel(); timer = nil
        if lockDescriptor >= 0 { flock(lockDescriptor, LOCK_UN); close(lockDescriptor); lockDescriptor = -1 }
        if let text = try? String(contentsOf: pidURL, encoding: .utf8),
           text.split(whereSeparator: \.isNewline).first.flatMap({ pid_t($0) }) == getpid() {
            try? FileManager.default.removeItem(at: pidURL)
        }
        exit(0)
    }

    private func log(_ text: String) {
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(text)\n"
        guard let data = line.data(using: .utf8) else { return }
        if FileManager.default.fileExists(atPath: logURL.path), let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd(); _ = try? handle.write(contentsOf: data); _ = try? handle.close()
        } else { try? data.write(to: logURL, options: .atomic) }
    }
}
