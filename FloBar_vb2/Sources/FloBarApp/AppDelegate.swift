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
    private var settingsSaveCoordinator: SettingsSaveCoordinator?
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
        app.setActivationPolicy(.regular)
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
        NSApp.applicationIconImage = Self.makeDockIcon()
        model.log("FloBar v2 started; startup sync scheduled once for this process.")
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if model.messageDraftDirty || settingsSaveCoordinator?.hasUnsavedChanges == true {
            let alert = NSAlert()
            alert.messageText = "Unsaved FloBar settings"
            alert.informativeText = "Save, discard, or cancel. If you quit FloBar without saving, these changes will be lost."
            alert.addButton(withTitle: "Save")
            alert.addButton(withTitle: "Discard")
            alert.addButton(withTitle: "Cancel")
            switch alert.runModal() {
            case .alertFirstButtonReturn:
                if saveOpenSettings() { quitting = true; return .terminateNow }
                quitting = false
                return .terminateCancel
            case .alertSecondButtonReturn:
                settingsSaveCoordinator?.discardAll()
                model.discardMessageDraft()
                quitting = true
                return .terminateNow
            default: quitting = false; return .terminateCancel
            }
        }
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        if quitting { model.saveRememberedTimerDefault(); writeGuardianCommand("quit") }
        settingsSaveCoordinator?.clear()
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
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "FloBar")
        let about = NSMenuItem(title: "About FloBar", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(about)
        appMenu.addItem(.separator())
        let settings = NSMenuItem(title: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        settings.target = self; appMenu.addItem(settings)
        appMenu.addItem(.separator())
        let hide = NSMenuItem(title: "Hide FloBar", action: #selector(hide), keyEquivalent: "h")
        hide.target = self; appMenu.addItem(hide)
        let hideOthers = NSMenuItem(title: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]; hideOthers.target = NSApp; appMenu.addItem(hideOthers)
        let showAll = NSMenuItem(title: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        showAll.target = NSApp; appMenu.addItem(showAll)
        let quit = NSMenuItem(title: "Quit FloBar", action: #selector(quit), keyEquivalent: "q")
        quit.target = self; appMenu.addItem(quit)
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        let undo = NSMenuItem(title: "Undo FloBar Settings", action: #selector(undo), keyEquivalent: "z")
        undo.target = self; editMenu.addItem(undo)
        let redo = NSMenuItem(title: "Redo FloBar Settings", action: #selector(redo), keyEquivalent: "Z")
        redo.keyEquivalentModifierMask = [.command, .shift]; redo.target = self; editMenu.addItem(redo)
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        let floBarItem = NSMenuItem()
        let floBarMenu = NSMenu(title: "FloBar")
        let restart = NSMenuItem(title: "Restart FloBar…", action: #selector(restartWithConfirmation), keyEquivalent: "")
        restart.target = self; floBarMenu.addItem(restart)
        floBarItem.submenu = floBarMenu
        mainMenu.addItem(floBarItem)
        NSApp.mainMenu = mainMenu
    }

    private func createFloatingPanel() {
        let edge = model.activeProfile.edge
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let stored = model.activeProfile.floatingFrame
        let frame: NSRect = model.activeProfile.docked ? dockedFrame(edge: edge, screen: screen) :
            stored.map { clamp(NSRect(x: $0.x, y: $0.y, width: $0.width, height: $0.height), to: screen) }
            ?? NSRect(x: screen.midX - 400, y: screen.midY - 44, width: 800, height: 88)
        let panel = FloBarPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel, .resizable], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.backgroundColor = NSColor(calibratedRed: 0.055, green: 0.075, blue: 0.105, alpha: 0.97)
        panel.isOpaque = false
        panel.hasShadow = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.acceptsMouseMovedEvents = true
        panel.minSize = edge.isVertical ? NSSize(width: 88, height: 420) : NSSize(width: 520, height: 88)
        panel.delegate = self
        panel.contentView = FloBarHostingView(rootView: AnyView(FloBarView(model: model, midi: midi, app: self)))
        panel.orderFrontRegardless()
        self.panel = panel
    }

    func repositionBar(_ edge: BarEdge) {
        guard let panel else { return }
        let screen = NSScreen.main?.visibleFrame ?? panel.screen?.visibleFrame ?? panel.frame
        let frame = dockedFrame(edge: edge, screen: screen)
        panel.setFrame(frame, display: true, animate: false)
        _ = model.savePanelPlacement(docked: true, edge: edge, frame: frame)
    }

    private func dockedFrame(edge: BarEdge, screen: NSRect) -> NSRect {
        edge.isVertical
            ? NSRect(x: edge == .left ? screen.minX : screen.maxX - 145, y: screen.minY + 40, width: 145, height: screen.height - 80)
            : NSRect(x: screen.minX, y: edge == .top ? screen.maxY - 88 : screen.minY, width: screen.width, height: 88)
    }

    private func clamp(_ frame: NSRect, to screen: NSRect) -> NSRect {
        var result = frame
        result.size.width = min(max(panel?.minSize.width ?? 520, result.width), screen.width)
        result.size.height = min(max(panel?.minSize.height ?? 88, result.height), screen.height)
        result.origin.x = min(max(result.origin.x, screen.minX), screen.maxX - result.width)
        result.origin.y = min(max(result.origin.y, screen.minY), screen.maxY - result.height)
        return result
    }

    func beginPanelDrag() { panelDragStartOrigin = panel?.frame.origin }

    func movePanel(by delta: NSPoint) {
        guard let panel else { return }
        let startOrigin = panelDragStartOrigin ?? panel.frame.origin
        var frame = panel.frame
        frame.origin = NSPoint(x: startOrigin.x + delta.x, y: startOrigin.y + delta.y)
        let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? frame
        panel.setFrame(clamp(frame, to: screen), display: true, animate: false)
    }

    func endPanelDrag() {
        guard let panel else { return }
        let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? panel.frame
        let frame = panel.frame
        let distances: [(BarEdge, CGFloat)] = [(.left, abs(frame.minX - screen.minX)), (.right, abs(screen.maxX - frame.maxX)),
                                                (.bottom, abs(frame.minY - screen.minY)), (.top, abs(screen.maxY - frame.maxY))]
        let nearest = distances.min { $0.1 < $1.1 }
        if let (edge, distance) = nearest, distance <= 52 {
            model.changeEdge(edge)
            panel.setFrame(dockedFrame(edge: edge, screen: screen), display: true, animate: true)
            _ = model.savePanelPlacement(docked: true, edge: edge, frame: panel.frame)
        } else {
            _ = model.savePanelPlacement(docked: false, edge: model.activeProfile.edge, frame: frame)
        }
        panelDragStartOrigin = nil
    }

    func windowDidEndLiveResize(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === panel else { return }
        let screen = panel?.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? window.frame
        let distances: [(BarEdge, CGFloat)] = [(.left, abs(window.frame.minX - screen.minX)), (.right, abs(screen.maxX - window.frame.maxX)),
                                                (.bottom, abs(window.frame.minY - screen.minY)), (.top, abs(screen.maxY - window.frame.maxY))]
        let nearest = distances.min { $0.1 < $1.1 }
        if let (edge, distance) = nearest, distance <= 24 {
            model.changeEdge(edge)
            window.setFrame(dockedFrame(edge: edge, screen: screen), display: true, animate: false)
            _ = model.savePanelPlacement(docked: true, edge: edge, frame: window.frame)
        } else {
            _ = model.savePanelPlacement(docked: false, edge: model.activeProfile.edge, frame: window.frame)
        }
    }

    func toggleFloatingBar() {
        guard let panel else { return }
        if panel.isVisible { panel.orderOut(nil) } else { panel.orderFrontRegardless() }
    }

    func activateBarButton(_ button: BarButton) { coordinator.activateBarButton(button) }

    func openSettings(selectWizard: Bool = false) {
        if let settingsWindow { settingsWindow.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        model.beginMessageDraft()
        let session = SettingsSaveCoordinator()
        settingsSaveCoordinator = session
        let root = SettingsRoot(model: model, app: self, startsOnWizard: selectWizard, saveCoordinator: session)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 680), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "FloBar Settings"
        window.level = .floating
        let screen = panel?.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? .zero
        var origin: NSPoint
        if let panel {
            switch model.activeProfile.edge {
            case .top: origin = NSPoint(x: panel.frame.minX, y: panel.frame.minY - window.frame.height)
            case .bottom: origin = NSPoint(x: panel.frame.minX, y: panel.frame.maxY)
            case .left: origin = NSPoint(x: panel.frame.maxX, y: screen.midY - window.frame.height / 2)
            case .right: origin = NSPoint(x: panel.frame.minX - window.frame.width, y: screen.midY - window.frame.height / 2)
            }
        } else { origin = NSPoint(x: screen.midX - window.frame.width / 2, y: screen.midY - window.frame.height / 2) }
        origin.x = min(max(origin.x, screen.minX), screen.maxX - window.frame.width)
        origin.y = min(max(origin.y, screen.minY), screen.maxY - window.frame.height)
        window.setFrameOrigin(origin)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = NSHostingView(rootView: root)
        settingsWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard model.messageDraftDirty || settingsSaveCoordinator?.hasUnsavedChanges == true else {
            settingsSaveCoordinator?.clear(); settingsWindow = nil; settingsSaveCoordinator = nil; return true
        }
        let alert = NSAlert()
        alert.messageText = "Unsaved FloBar settings"
        alert.informativeText = "Save, discard, or cancel. Unsaved changes will be lost when FloBar closes."
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Discard")
        alert.addButton(withTitle: "Cancel")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            guard saveOpenSettings() else { return false }
            settingsSaveCoordinator?.clear(); settingsWindow = nil; settingsSaveCoordinator = nil; return true
        }
        if response == .alertSecondButtonReturn {
            settingsSaveCoordinator?.discardAll(); model.discardMessageDraft()
            settingsSaveCoordinator?.clear(); settingsWindow = nil; settingsSaveCoordinator = nil; return true
        }
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

    func closeSettingsWindow() { settingsWindow?.performClose(nil) }
    func quitFromBar() { quit() }

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

    @objc private func showAbout() { NSApp.orderFrontStandardAboutPanel(nil) }
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
        quitting = true
        NSApp.terminate(nil)
    }

    func saveMessageDraftWithConfirmation(as name: String? = nil) -> Bool {
        guard model.messageDraftDirty else { return true }
        guard let result = model.saveMessageDraft(as: name) else { return true }
        guard result.hasPrefix("EXISTS:") else { model.lastError = result; return false }
        let existingName = String(result.dropFirst("EXISTS:".count))
        let alert = NSAlert()
        alert.messageText = "Replace \(existingName)?"
        alert.informativeText = "Saving over this Set will replace its current settings. FloBar keeps one previous-Set snapshot; a later overwrite replaces that snapshot, and the older settings cannot then be recovered."
        alert.addButton(withTitle: "Overwrite \(existingName)")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        if let error = model.saveMessageDraft(as: existingName, confirmedOverwrite: true) {
            model.lastError = error
            return false
        }
        return true
    }

    private func saveOpenSettings() -> Bool {
        if let settingsSaveCoordinator, !settingsSaveCoordinator.saveAll() { return false }
        if model.messageDraftDirty { return saveMessageDraftWithConfirmation() }
        return true
    }

    private static func makeDockIcon() -> NSImage {
        let image = NSImage(size: NSSize(width: 128, height: 128), flipped: false) { rect in
            let background = NSBezierPath(roundedRect: rect.insetBy(dx: 3, dy: 3), xRadius: 28, yRadius: 28)
            NSColor(calibratedRed: 0.08, green: 0.19, blue: 0.29, alpha: 1).setFill(); background.fill()
            let accent = NSBezierPath(roundedRect: NSRect(x: 18, y: 17, width: 92, height: 94), xRadius: 18, yRadius: 18)
            NSColor(calibratedRed: 0.20, green: 0.48, blue: 0.68, alpha: 1).setFill(); accent.fill()
            let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 50, weight: .black), .foregroundColor: NSColor.white]
            let title = NSAttributedString(string: "F", attributes: attributes)
            let size = title.size()
            title.draw(at: NSPoint(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2 - 2))
            return true
        }
        return image
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

}
