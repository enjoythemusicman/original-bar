import AppKit
import CoreMIDI
import Darwin
import FloBarCore
import Foundation
import ServiceManagement
import SwiftUI

final class FloBarPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class FloBarHostingView: NSHostingView<AnyView> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

@MainActor
@main
final class FloBarApplication: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var model: FloBarModel!
    private var midi: CoreMIDIService!
    private var batteryMonitor: BluetoothBatteryMonitor!
    private var coordinator: MIDIActionCoordinator!
    private var inputMonitor = GlobalInputMonitor()
    private var panel: FloBarPanel?
    private var settingsWindow: NSWindow?
    private var heartbeatTimer: Timer?
    private var guardianProcess: Process?
    private var thermalObserver: NSObjectProtocol?
    private var quitting = false
    private var restartRequested = false
    private var guardianRestartAttempted = false
    private var panelDragStartOrigin: NSPoint?

    static func main() {
        let app = NSApplication.shared
        let bundleID = Bundle.main.bundleIdentifier ?? "com.galtalor.FloBar"
        if let existing = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            existing.activate(options: [.activateIgnoringOtherApps])
            return
        }
        let delegate = FloBarApplication()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        model = FloBarModel()
        midi = CoreMIDIService()
        batteryMonitor = BluetoothBatteryMonitor()
        batteryMonitor.onSnapshot = { [weak self] connected, batteries in
            Task { @MainActor in self?.model.updateBluetoothSnapshot(connected: connected, batteries: batteries) }
        }
        coordinator = MIDIActionCoordinator(midi: midi, model: model, initialProfile: model.activeProfile)
        coordinator.updateProfile(model.activeProfile)
        model.onConfigurationChanged = { [weak coordinator] profile in coordinator?.updateProfile(profile) }
        midi.onEvent = { [weak self] event, source in
            self?.coordinator.receive(event, source: source)
        }
        midi.onOutputSent = { [weak self] event, origin in
            self?.coordinator.recordOutput(event, origin: origin)
        }
        midi.onEndpointsChanged = { [weak self] inputs, outputs in
            Task { @MainActor in
                self?.model.availableInputs = inputs
                self?.model.availableOutputs = outputs
                self?.refreshControllerConnection(inputs)
            }
            if outputs.contains(where: { $0.localizedCaseInsensitiveContains("IAC Driver Bus 1") }) {
                self?.midi.sendStartupSyncOnce()
            }
        }
        coordinator.onHide = { [weak self] in self?.toggleFloatingBar() }
        coordinator.onRestart = { [weak self] in self?.requestManualRestart() }
        coordinator.onLayoutChanged = { [weak self] split in
            Task { @MainActor in self?.model.controllerLines[.air] = split ? "j/sF" : "j/sF" }
        }

        buildMainMenu()
        createFloatingPanel()
        midi.start()
        midi.sendStartupSyncOnce()
        batteryMonitor.start()
        startHeartbeat()
        startThermalMonitor()
        startGuardianIfPackaged()
        inputMonitor.onSpecialKey = { [weak self] key in
            guard let self else { return }
            if key == "§" { self.coordinator.manualRestart() }
            else { self.coordinator.receive(.hid(key: key), source: "Global HID") }
        }
        if model.activeProfile.globalHideShortcutEnabled { requestGlobalInputMonitoring() }
        if model.showSetupWizard { openSettings(selectWizard: true) }
        model.log("FloBar v1 started; startup sync scheduled once for this process.")
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateNow }
        if model.messageDraftDirty {
            let alert = NSAlert()
            alert.messageText = "Unsaved message changes"
            alert.informativeText = "Save, discard, or cancel. If you close FloBar without saving, these changes will be lost."
            alert.addButton(withTitle: "Save")
            alert.addButton(withTitle: "Discard")
            alert.addButton(withTitle: "Cancel")
            switch alert.runModal() {
            case .alertFirstButtonReturn:
                if saveDraftWithConfirmation() { return .terminateNow }
                return .terminateCancel
            case .alertSecondButtonReturn: model.discardMessageDraft(); return .terminateNow
            default: return .terminateCancel
            }
        }
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        if quitting { model.saveRememberedTimerDefault() }
        heartbeatTimer?.invalidate()
        inputMonitor.stop()
        midi.stop()
        batteryMonitor.stop()
        if let thermalObserver { NotificationCenter.default.removeObserver(thermalObserver) }
        let recoveryMarker = model.appSupportURL.appendingPathComponent("runtime/guardian-recovery.txt")
        let guardianIsRecovering = FileManager.default.fileExists(atPath: recoveryMarker.path)
        if !quitting && !restartRequested && !guardianIsRecovering { writeGuardianCommand("quit") }
    }

    private func buildMainMenu() {
        let menu = NSMenu()
        let settings = NSMenuItem(title: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        settings.target = self; menu.addItem(settings)
        menu.addItem(.separator())
        let undo = NSMenuItem(title: "Undo FloBar Settings", action: #selector(undo), keyEquivalent: "z")
        undo.target = self; menu.addItem(undo)
        let redo = NSMenuItem(title: "Redo FloBar Settings", action: #selector(redo), keyEquivalent: "Z")
        redo.keyEquivalentModifierMask = [.command, .shift]; redo.target = self; menu.addItem(redo)
        let restart = NSMenuItem(title: "Restart FloBar…", action: #selector(restartWithConfirmation), keyEquivalent: "")
        restart.target = self; menu.addItem(restart)
        let hide = NSMenuItem(title: "Hide FloBar", action: #selector(hide), keyEquivalent: "h")
        hide.target = self; menu.addItem(hide)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit FloBar", action: #selector(quit), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        NSApp.mainMenu = menu
    }

    private func createFloatingPanel() {
        let edge = model.activeProfile.edge
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let frame: NSRect = edge.isVertical ? NSRect(x: screen.minX, y: screen.minY + 40, width: 145, height: screen.height - 80)
                                            : NSRect(x: screen.minX, y: edge == .top ? screen.maxY - 76 : screen.minY, width: screen.width, height: 76)
        let panel = FloBarPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.backgroundColor = NSColor(calibratedRed: 0.055, green: 0.075, blue: 0.105, alpha: 0.97)
        panel.isOpaque = false
        panel.hasShadow = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.acceptsMouseMovedEvents = true
        panel.contentView = FloBarHostingView(rootView: AnyView(FloBarView(model: model, midi: midi, app: self)))
        panel.orderFrontRegardless()
        self.panel = panel
    }

    func repositionBar(_ edge: BarEdge) {
        guard let panel else { return }
        let screen = NSScreen.main?.visibleFrame ?? panel.screen?.visibleFrame ?? panel.frame
        let frame: NSRect = edge.isVertical ? NSRect(x: edge == .left ? screen.minX : screen.maxX - 145, y: screen.minY + 40, width: 145, height: screen.height - 80)
                                            : NSRect(x: screen.minX, y: edge == .top ? screen.maxY - 76 : screen.minY, width: screen.width, height: 76)
        panel.setFrame(frame, display: true, animate: false)
    }

    func beginPanelDrag() { panelDragStartOrigin = panel?.frame.origin }

    func movePanel(by delta: NSPoint) {
        guard let panel else { return }
        let startOrigin = panelDragStartOrigin ?? panel.frame.origin
        var snappedEdge: BarEdge?
        if max(abs(delta.x), abs(delta.y)) >= 42 {
            if abs(delta.x) > abs(delta.y) { snappedEdge = delta.x < 0 ? .left : .right }
            else { snappedEdge = delta.y > 0 ? .top : .bottom }
        }
        if let snappedEdge {
            panelDragStartOrigin = nil
            model.changeEdge(snappedEdge)
            repositionBar(snappedEdge)
            return
        }
        var frame = panel.frame
        frame.origin = NSPoint(x: startOrigin.x + delta.x, y: startOrigin.y + delta.y)
        let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? frame
        frame.origin.x = min(max(frame.origin.x, screen.minX), screen.maxX - frame.width)
        frame.origin.y = min(max(frame.origin.y, screen.minY), screen.maxY - frame.height)
        panel.setFrameOrigin(frame.origin)
    }

    func toggleFloatingBar() {
        guard let panel else { return }
        if panel.isVisible { panel.orderOut(nil) } else { panel.orderFrontRegardless() }
    }

    func activateBarButton(_ button: BarButton) { coordinator.activateBarButton(button) }

    func openSettings(selectWizard: Bool = false) {
        if let settingsWindow { settingsWindow.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let root = SettingsRoot(model: model, app: self, startsOnWizard: selectWizard)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 680), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "FloBar Settings"
        window.center()
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = NSHostingView(rootView: root)
        settingsWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard model.messageDraftDirty else { settingsWindow = nil; return true }
        let alert = NSAlert()
        alert.messageText = "Unsaved message changes"
        alert.informativeText = "Save, discard, or cancel. If you close the settings window without saving, these changes will be lost when FloBar closes."
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Discard")
        alert.addButton(withTitle: "Cancel")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            guard saveDraftWithConfirmation() else { return false }
            settingsWindow = nil; return true
        }
        if response == .alertSecondButtonReturn { model.discardMessageDraft(); settingsWindow = nil; return true }
        return false
    }

    func requestGlobalInputMonitoring(optedIn: Bool = false) {
        guard optedIn || model.activeProfile.globalHideShortcutEnabled else {
            model.lastError = "Enable the optional global keyboard feature first. FloBar does not request keyboard access unless you opt in."
            return
        }
        _ = ExternalActions().requestListenEventAccess()
        guard inputMonitor.startIfAuthorized() else {
            model.lastError = "Global HID and § monitoring is not available. Enable Input Monitoring for FloBar in System Settings, then try again. No keystrokes are stored."
            model.log("Global input monitoring unavailable or not authorized.")
            return
        }
        model.log("Global input monitoring enabled by user setting.")
    }

    func registerLoginItem(_ enabled: Bool) throws {
        guard #available(macOS 13.0, *) else { throw NSError(domain: "FloBar", code: 13, userInfo: [NSLocalizedDescriptionKey: "The supported Apple login-item API requires macOS 13 or later."]) }
        if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        _ = model.mutate("Login Item Setting") { config in
            config.startOnLogin = enabled
            if let index = config.profiles.firstIndex(where: { $0.id == config.activeProfileID }) { config.profiles[index].loginItemEnabled = enabled }
        }
    }

    func importLegacySettings() {
        let panel = NSOpenPanel()
        panel.title = "Choose the legacy message settings file"
        panel.message = "FloBar reads only the file you select. It does not scan other folders."
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try model.importLegacyMessageSettings(from: url)
            model.log("User-selected legacy settings file imported: \(url.lastPathComponent)")
        } catch { model.lastError = "Import failed: \(error.localizedDescription)" }
    }

    func exportBackup() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "FloBar-settings-backup.json"
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try JSONEncoder.pretty.encode(model.configuration).write(to: url, options: .atomic) }
        catch { model.lastError = "Backup export failed: \(error.localizedDescription)" }
    }

    func importBackup() {
        let panel = NSOpenPanel()
        panel.title = "Choose a FloBar backup"
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let incoming = try JSONDecoder().decode(FloBarConfiguration.self, from: Data(contentsOf: url))
            guard incoming.schemaVersion == 1,
                  !incoming.profiles.isEmpty,
                  !incoming.messageSets.isEmpty,
                  incoming.profiles.contains(where: { $0.id == incoming.activeProfileID }),
                  incoming.messageSets.contains(where: { $0.id == incoming.activeMessageSetID }) else {
                throw NSError(domain: "FloBar", code: 1, userInfo: [NSLocalizedDescriptionKey: "This backup is incomplete, invalid, or uses an unsupported schema."])
            }
            let setNames = incoming.messageSets.map(\.name).joined(separator: ", ")
            let alert = NSAlert(); alert.messageText = "Replace FloBar settings?"
            alert.informativeText = "Preview: \(incoming.profiles.count) Profile(s), \(incoming.messageSets.count) Message Set(s): \(setNames). The selected file is schema v\(incoming.schemaVersion). FloBar first saves the current valid configuration to \(model.preImportBackupURL.path)."
            alert.addButton(withTitle: "Import"); alert.addButton(withTitle: "Cancel")
            if alert.runModal() == .alertFirstButtonReturn,
               !model.replaceConfigurationFromUserImport(incoming) {
                model.lastError = "The selected backup could not be saved. The previous settings remain unchanged."
            }
        } catch { model.lastError = "Backup import failed: \(error.localizedDescription)" }
    }

    func writeGuardianCommand(_ command: String) {
        let url = model.appSupportURL.appendingPathComponent("runtime/guardian-command.txt")
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? command.write(to: url, atomically: true, encoding: .utf8)
    }

    func requestManualRestart() {
        let pidURL = model.appSupportURL.appendingPathComponent("runtime/guardian.pid")
        let fields = (try? String(contentsOf: pidURL, encoding: .utf8))?.split(whereSeparator: \.isNewline) ?? []
        let guardianIsHealthy = fields.count >= 2
            && Int32(fields[0]).map { kill($0, 0) == 0 } == true
            && Double(fields[1]).map { ProcessInfo.processInfo.systemUptime - $0 < 3 } == true
        if !guardianIsHealthy {
            // A standalone development launch may have no embedded guardian. Fail visibly
            // instead of starting a competing restart authority.
            model.lastError = "The FloBar guardian is not running, so a safe § restart cannot be completed."
            return
        }
        restartRequested = true
        writeGuardianCommand("restart")
        model.log("Manual § restart requested.")
    }

    @objc private func showSettings() { openSettings() }
    @objc private func undo() { model.undo() }
    @objc private func redo() { model.redo() }
    @objc private func hide() { panel?.orderOut(nil) }
    @objc private func restartWithConfirmation() {
        let alert = NSAlert(); alert.messageText = "Restart FloBar?"; alert.informativeText = "The current MIDI sequence, if any, will be abandoned. Startup synchronization will run once in the new process."
        alert.addButton(withTitle: "Restart"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { requestManualRestart() }
    }
    @objc private func quit() {
        let alert = NSAlert(); alert.messageText = "Quit FloBar?"; alert.informativeText = "Quit stops the bar and guardian. Hide leaves both running."
        alert.addButton(withTitle: "Quit FloBar"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { quitting = true; writeGuardianCommand("quit"); NSApp.terminate(nil) }
    }

    private func startHeartbeat() {
        model.recordHeartbeat()
        heartbeatTimer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.model.recordHeartbeat() }
        }
        if let heartbeatTimer { RunLoop.main.add(heartbeatTimer, forMode: .common) }
    }

    private func startThermalMonitor() {
        updateThermalLevel()
        thermalObserver = NotificationCenter.default.addObserver(forName: ProcessInfo.thermalStateDidChangeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.updateThermalLevel() }
        }
    }

    private func updateThermalLevel() {
        let state = ProcessInfo.processInfo.thermalState
        model.thermalLevel = state == .critical ? 2 : state == .serious ? 1 : 0
    }

    private func refreshControllerConnection(_ inputs: [String]) {
        model.setController(.air, online: inputs.contains { $0.localizedCaseInsensitiveContains("airstep") })
        model.setController(.smc, online: inputs.contains { $0.localizedCaseInsensitiveContains("smc") })
    }

    private func startGuardianIfPackaged() {
        let support = model.appSupportURL.appendingPathComponent("runtime", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let pidURL = support.appendingPathComponent("guardian.pid")
        let guardianState = (try? String(contentsOf: pidURL, encoding: .utf8))?.split(whereSeparator: \.isNewline) ?? []
        if guardianState.count >= 2,
           let pid = Int32(guardianState[0]), kill(pid, 0) == 0,
           let heartbeat = Double(guardianState[1]), ProcessInfo.processInfo.systemUptime - heartbeat < 3 {
            model.log("A FloBar guardian is already running for this user session.")
            return
        }
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let embedded = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/FloBarGuardian")
        let sibling = executable.deletingLastPathComponent().appendingPathComponent("FloBarGuardian")
        let helper = FileManager.default.fileExists(atPath: embedded.path) ? embedded : sibling
        guard FileManager.default.isExecutableFile(atPath: helper.path), Bundle.main.bundleURL.pathExtension == "app" else {
            model.log("Guardian not launched: packaged .app with embedded helper is required.")
            return
        }
        let process = Process()
        process.executableURL = helper
        process.arguments = ["--app", Bundle.main.bundleURL.path,
                             "--heartbeat", model.heartbeatURL.path,
                             "--recovery-marker", support.appendingPathComponent("guardian-recovery.txt").path,
                             "--command", support.appendingPathComponent("guardian-command.txt").path,
                             "--log", model.logsURL.appendingPathComponent("Guardian.log").path]
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, !self.quitting, self.guardianProcess === process, !self.guardianRestartAttempted else { return }
                self.guardianRestartAttempted = true
                self.model.log("Guardian exited while FloBar was alive; attempting its single permitted restart.")
                self.guardianProcess = nil
                self.startGuardianIfPackaged()
            }
        }
        do { try process.run(); guardianProcess = process }
        catch { model.log("Could not start bundled guardian: \(error)") }
    }

    private func saveDraftWithConfirmation() -> Bool {
        guard let result = model.saveMessageDraft() else { return true }
        guard result.hasPrefix("EXISTS:") else { model.lastError = result; return false }
        let existingName = String(result.dropFirst("EXISTS:".count))
        let alert = NSAlert()
        alert.messageText = "Replace \(existingName)?"
        alert.informativeText = "Saving over this Set will replace its current settings. FloBar keeps one previous-Set snapshot; a later overwrite replaces that snapshot, and the older settings cannot then be recovered."
        alert.addButton(withTitle: "Overwrite \(existingName)")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        if let error = model.saveMessageDraft(as: existingName, confirmedOverwrite: true) { model.lastError = error; return false }
        return true
    }
}
