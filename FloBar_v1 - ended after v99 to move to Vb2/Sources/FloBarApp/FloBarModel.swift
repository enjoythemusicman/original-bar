import AppKit
import Combine
import Foundation
import FloBarCore

@MainActor
final class FloBarModel: ObservableObject {
    @Published private(set) var configuration: FloBarConfiguration
    @Published var currentMessageIndex = 0
    @Published var currentMIDIText = "MIDI INPUT"
    @Published var lastReceivedEvent: MIDIEvent?
    @Published var lastReceivedSource = ""
    @Published private(set) var midiEventGeneration: UInt64 = 0
    @Published var availableInputs: [String] = []
    @Published var availableOutputs: [String] = []
    @Published var temporarilyHiddenMessageIDs: Set<UUID> = []
    @Published var messageDraft: MessageSet?
    @Published var messageDraftDirty = false
    @Published var controllerLines: [ControllerKind: String] = [:]
    @Published var controllerOnline: Set<ControllerKind> = []
    @Published var controllerConnectionKnown: Set<ControllerKind> = []
    @Published var batteryLevels: [ControllerKind: Int] = [:]
    @Published var lastCommandController: ControllerKind = .unknown
    @Published var lastCommandName: String?
    @Published var pairedIACText: String?
    @Published var displayIACText = false
    @Published var stateColors: [Int: String] = [:]
    @Published var thermalLevel = 0
    @Published var settingsPresented = false
    @Published var lastError: String?
    @Published var showSetupWizard = false
    @Published var timerRemaining: TimeInterval = 45 * 60
    @Published var timerRunning = false
    @Published var timerOvertime = false
    @Published var startupStorageError: String?
    var onConfigurationChanged: ((BarProfile) -> Void)?

    private struct Snapshot { var label: String; var data: Data }
    private var undoStack: [Snapshot] = []
    private var redoStack: [Snapshot] = []
    private let fileManager = FileManager.default
    private(set) var appSupportURL: URL
    private(set) var logsURL: URL
    private var timerDeadline: TimeInterval?
    private var timer: Timer?
    private var iacAlternationTimer: Timer?
    private var refusesWritesUntilExplicitImport = false

    var activeProfile: BarProfile {
        configuration.profiles.first(where: { $0.id == configuration.activeProfileID }) ?? configuration.profiles[0]
    }
    var activeMessageSet: MessageSet {
        configuration.messageSets.first(where: { $0.id == configuration.activeMessageSetID }) ?? configuration.messageSets[0]
    }
    var visibleMessages: [MessageRow] { activeMessageSet.rows.filter { $0.show && !$0.text.isEmpty && !temporarilyHiddenMessageIDs.contains($0.id) } }
    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }
    var mainSettingsURL: URL { appSupportURL.appendingPathComponent("settings.json") }
    var previousSetSnapshotURL: URL { appSupportURL.appendingPathComponent("previous-message-set.json") }
    var preImportBackupURL: URL { appSupportURL.appendingPathComponent("before-backup-import.json") }
    var heartbeatURL: URL { appSupportURL.appendingPathComponent("runtime/heartbeat.txt") }

    init() {
        let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support", isDirectory: true)
        let library = fileManager.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Library", isDirectory: true)
        let logs = library.appendingPathComponent("Logs", isDirectory: true).appendingPathComponent("FloBar", isDirectory: true)
        appSupportURL = support.appendingPathComponent("FloBar", isDirectory: true)
        logsURL = logs
        try? fileManager.createDirectory(at: appSupportURL, withIntermediateDirectories: true)
        try? fileManager.createDirectory(at: logsURL, withIntermediateDirectories: true)
        let url = appSupportURL.appendingPathComponent("settings.json")
        if fileManager.fileExists(atPath: url.path) {
            do {
                let loaded = try JSONDecoder().decode(FloBarConfiguration.self, from: Data(contentsOf: url))
                guard !loaded.profiles.isEmpty, !loaded.messageSets.isEmpty,
                      loaded.profiles.contains(where: { $0.id == loaded.activeProfileID }),
                      loaded.messageSets.contains(where: { $0.id == loaded.activeMessageSetID }) else {
                    throw NSError(domain: "FloBar.Settings", code: 1, userInfo: [NSLocalizedDescriptionKey: "The saved settings have invalid active Profile or Message Set references."])
                }
                configuration = loaded
            } catch {
                configuration = FloBarConfiguration()
                refusesWritesUntilExplicitImport = true
                startupStorageError = "FloBar could not read its existing settings and has preserved the file without overwriting it. Import a valid FloBar backup in Settings → Data & Access to continue. Details: \(error.localizedDescription)"
                lastError = startupStorageError
            }
        } else {
            configuration = FloBarConfiguration()
        }
        timerRemaining = TimeInterval(activeProfile.timerDefaultSeconds)
        showSetupWizard = !configuration.wizardCompleted
    }

    func log(_ line: String) {
        let url = logsURL.appendingPathComponent("FloBar.log")
        let entry = "[\(ISO8601DateFormatter().string(from: Date()))] \(line)\n"
        if let data = entry.data(using: .utf8) {
            if fileManager.fileExists(atPath: url.path), let handle = try? FileHandle(forWritingTo: url) {
                _ = try? handle.seekToEnd(); _ = try? handle.write(contentsOf: data); _ = try? handle.close()
            } else { try? data.write(to: url, options: .atomic) }
        }
    }

    func recordHeartbeat() {
        let runtime = appSupportURL.appendingPathComponent("runtime", isDirectory: true)
        try? fileManager.createDirectory(at: runtime, withIntermediateDirectories: true)
        let body = "\(ProcessInfo.processInfo.systemUptime)\n\(ProcessInfo.processInfo.processIdentifier)\n"
        try? body.write(to: heartbeatURL, atomically: true, encoding: .utf8)
    }

    private func encodedConfiguration() throws -> Data { try JSONEncoder.pretty.encode(configuration) }
    private func remember(_ label: String) {
        guard let data = try? encodedConfiguration() else { return }
        undoStack.append(.init(label: label, data: data))
        if undoStack.count > 20 { undoStack.removeFirst(undoStack.count - 20) }
        redoStack.removeAll()
    }

    @discardableResult
    func mutate(_ label: String, _ operation: (inout FloBarConfiguration) -> Void) -> Bool {
        let before = configuration
        let previousUndoCount = undoStack.count
        remember(label)
        operation(&configuration)
        guard persist() else {
            configuration = before
            if undoStack.count > previousUndoCount { undoStack.removeLast() }
            return false
        }
        return true
    }

    @discardableResult
    func persist() -> Bool {
        guard !refusesWritesUntilExplicitImport else {
            lastError = startupStorageError ?? "Settings are read-only until a valid backup is explicitly imported."
            return false
        }
        do {
            let data = try encodedConfiguration()
            try data.write(to: mainSettingsURL, options: .atomic)
            onConfigurationChanged?(activeProfile)
            return true
        } catch {
            lastError = "FloBar could not save settings: \(error.localizedDescription)"
            log("SETTINGS WRITE FAILED: \(error)")
            return false
        }
    }

    func undo() {
        guard let previous = undoStack.popLast(), let current = try? encodedConfiguration(),
              let restored = try? JSONDecoder().decode(FloBarConfiguration.self, from: previous.data) else { return }
        let original = configuration
        configuration = restored
        guard persist() else { configuration = original; undoStack.append(previous); return }
        redoStack.append(.init(label: previous.label, data: current))
    }

    func redo() {
        guard let next = redoStack.popLast(), let current = try? encodedConfiguration(),
              let restored = try? JSONDecoder().decode(FloBarConfiguration.self, from: next.data) else { return }
        let original = configuration
        configuration = restored
        guard persist() else { configuration = original; redoStack.append(next); return }
        undoStack.append(.init(label: next.label, data: current))
    }

    func createMessageSet(named rawName: String) -> Bool {
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !configuration.messageSets.contains(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) else { return false }
        return mutate("Add Message Set") {
            let set = MessageSet(name: name)
            $0.messageSets.append(set)
            $0.activeMessageSetID = set.id
        }
    }

    func selectMessageSet(_ id: UUID) { _ = mutate("Load Message Set") { $0.activeMessageSetID = id } }

    func beginMessageDraft() { messageDraft = activeMessageSet; messageDraftDirty = false }

    func updateMessageDraft(_ operation: (inout MessageSet) -> Void) {
        guard var draft = messageDraft else { return }
        operation(&draft)
        draft.rows = draft.rows.map { row in
            var copy = row
            copy.text = String(copy.text.prefix(40))
            copy.seconds = max(1, copy.seconds)
            return copy
        }
        messageDraft = draft; messageDraftDirty = true
    }

    func discardMessageDraft() { beginMessageDraft() }

    private func preparePreviousSetSnapshot(_ existing: MessageSet) throws -> Data? {
        let oldSnapshot: Data? = fileManager.fileExists(atPath: previousSetSnapshotURL.path)
            ? try Data(contentsOf: previousSetSnapshotURL) : nil
        let newSnapshot = try JSONEncoder.pretty.encode(existing)
        try newSnapshot.write(to: previousSetSnapshotURL, options: .atomic)
        return oldSnapshot
    }

    private func rollBackPreviousSetSnapshot(_ oldSnapshot: Data?) {
        if let oldSnapshot { try? oldSnapshot.write(to: previousSetSnapshotURL, options: .atomic) }
        else { try? fileManager.removeItem(at: previousSetSnapshotURL) }
    }

    func saveMessageDraft(as rawName: String? = nil, confirmedOverwrite: Bool = false) -> String? {
        guard let draft = messageDraft else { return "No message draft is open." }
        let name = (rawName ?? draft.name).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return "Enter a Set name." }
        if let existing = configuration.messageSets.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
            guard confirmedOverwrite else { return "EXISTS:\(existing.name)" }
            let oldSnapshot: Data?
            do { oldSnapshot = try preparePreviousSetSnapshot(existing) }
            catch { return "Could not save the previous-Set snapshot: \(error.localizedDescription)" }
            guard mutate("Save Message Set", { config in
                var replacement = draft
                replacement.id = existing.id
                replacement.name = existing.name
                if let i = config.messageSets.firstIndex(where: { $0.id == existing.id }) { config.messageSets[i] = replacement }
                config.activeMessageSetID = existing.id
            }) else {
                rollBackPreviousSetSnapshot(oldSnapshot)
                return "Could not save Set changes to disk."
            }
        } else {
            guard mutate("Add Message Set", { config in
                var addition = draft
                addition.id = UUID()
                addition.name = name
                config.messageSets.append(addition)
                config.activeMessageSetID = addition.id
            }) else { return "Could not save the new Set to disk." }
        }
        beginMessageDraft()
        return nil
    }

    func saveMessageSet(as requestedName: String? = nil, confirmedOverwrite: Bool = false) -> String? {
        let current = activeMessageSet
        let name = (requestedName ?? current.name).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return "Enter a Set name." }
        if let existing = configuration.messageSets.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
            if !confirmedOverwrite { return "EXISTS:\(existing.name)" }
            let oldSnapshot: Data?
            do { oldSnapshot = try preparePreviousSetSnapshot(existing) }
            catch { return "Could not save the previous-Set snapshot: \(error.localizedDescription)" }
            guard mutate("Save Message Set", { config in
                guard let i = config.messageSets.firstIndex(where: { $0.id == existing.id }) else { return }
                var replacement = current
                replacement.name = existing.name
                config.messageSets[i] = replacement
                config.activeMessageSetID = existing.id
            }) else {
                rollBackPreviousSetSnapshot(oldSnapshot)
                return "Could not save Set changes to disk."
            }
            return nil
        }
        guard mutate("Add Message Set", { config in
            var copy = current
            copy.id = UUID()
            copy.name = name
            config.messageSets.append(copy)
            config.activeMessageSetID = copy.id
        }) else { return "Could not save the new Set to disk." }
        return nil
    }

    func restorePreviousSet() -> String? {
        guard let data = try? Data(contentsOf: previousSetSnapshotURL), let previous = try? JSONDecoder().decode(MessageSet.self, from: data) else { return nil }
        guard mutate("Restore Previous Message Set", { config in
            if let index = config.messageSets.firstIndex(where: { $0.name.caseInsensitiveCompare(previous.name) == .orderedSame }) {
                var restored = previous
                restored.id = config.messageSets[index].id
                config.messageSets[index] = restored
                config.activeMessageSetID = restored.id
            } else {
                config.messageSets.append(previous)
                config.activeMessageSetID = previous.id
            }
        }) else { return nil }
        return previous.name
    }

    func editMessage(_ id: UUID, text: String? = nil, seconds: Int? = nil, shown: Bool? = nil) -> Bool {
        if let text, !validMessageText(text) { return false }
        return mutate("Edit Message") { config in
            guard let setIndex = config.messageSets.firstIndex(where: { $0.id == config.activeMessageSetID }),
                  let rowIndex = config.messageSets[setIndex].rows.firstIndex(where: { $0.id == id }) else { return }
            if let text { config.messageSets[setIndex].rows[rowIndex].text = text }
            if let seconds { config.messageSets[setIndex].rows[rowIndex].seconds = max(1, seconds) }
            if let shown { config.messageSets[setIndex].rows[rowIndex].show = shown }
        }
    }

    func setController(_ kind: ControllerKind, online: Bool) {
        controllerConnectionKnown.insert(kind)
        if online { controllerOnline.insert(kind) } else { controllerOnline.remove(kind) }
        if !online { stateColors.removeAll() }
    }

    func updateBluetoothSnapshot(connected: [ControllerKind: Bool], batteries: [ControllerKind: Int]) {
        for kind in [ControllerKind.smc, .p23] {
            if let isConnected = connected[kind] { setController(kind, online: isConnected) }
        }
        batteryLevels = batteries.filter { [ControllerKind.smc, .p23].contains($0.key) && (0...100).contains($0.value) }
    }

    func receive(_ event: MIDIEvent, from sourceName: String) {
        midiEventGeneration &+= 1
        lastReceivedEvent = event
        lastReceivedSource = sourceName
        let kind: ControllerKind = {
            let s = sourceName.lowercased()
            if s.contains("airstep") { return .air }
            if s.contains("smc") { return .smc }
            if s.contains("p23") { return .p23 }
            if s.contains("iac") || s.contains("bus 2") { return .iac }
            return .unknown
        }()
        if kind == .iac {
            return
        }
        currentMIDIText = event.rawLabel
        iacAlternationTimer?.invalidate(); pairedIACText = nil; displayIACText = false
        if case .hid = event {
            lastCommandController = .p23
            lastCommandName = event.rawLabel
            controllerLines[.p23] = event.rawLabel
            return
        }
        lastCommandName = commandShortName(for: event, kind: kind) ?? event.rawLabel
        lastCommandController = kind
        controllerLines[kind] = lastCommandName
        if kind != .unknown { controllerOnline.insert(kind) }
    }

    func recordPairedIACOutput(_ event: MIDIEvent, sourceText: String) {
        currentMIDIText = sourceText
        pairedIACText = event.rawLabel
        displayIACText = false
        iacAlternationTimer?.invalidate()
        iacAlternationTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.displayIACText.toggle() }
        }
    }

    func commandShortName(for event: MIDIEvent, kind: ControllerKind) -> String? {
        if case .hid = event { return kind == .p23 ? event.rawLabel : nil }
        if let configured = activeProfile.buttons.first(where: { button in
            ([button.command] + button.inputAliases).contains { command in
                guard command.number >= 0 else { return false }
                switch (event, command.kind) {
                case let (.cc(channel, number, _), .controlChange): return channel == command.channel && number == command.number
                case let (.noteOn(channel, number, _), .noteOn), let (.noteOff(channel, number), .noteOn): return channel == command.channel && number == command.number
                case let (.hid(key), .hidKey): return key.caseInsensitiveCompare(command.key) == .orderedSame
                default: return false
                }
            }
        }) {
            return configured.command.shortName.isEmpty ? configured.shortTitle : configured.command.shortName
        }
        if case let .noteOn(_, n, _) = event {
            switch n { case 18: return "gtM"; case 22: return "fxM"; case 93: return "fbH"; case 94: return "j/sF"; default: return nil }
        }
        guard case let .cc(channel, number, _) = event else { return nil }
        if channel == 16 {
            switch number {
            case 42: return "G2M"
            case 41: return "MM"
            case 39: return "Bvox"
            case 40: return "Solos"
            case 43: return "fxSC"
            case 44: return "TT"
            case 45: return "TB"
            case 100: return "L"
            case 101: return "GBk"
            default: break
            }
        }
        if kind == .iac { return "IAC" }
        return nil
    }

    func advanceMessage(_ delta: Int) {
        let rows = visibleMessages
        guard !rows.isEmpty else { currentMessageIndex = 0; return }
        currentMessageIndex = (currentMessageIndex + delta + rows.count) % rows.count
    }

    func temporaryHideCurrentMessage() {
        let rows = visibleMessages
        guard !rows.isEmpty, currentMessageIndex < rows.count else { return }
        let id = rows[currentMessageIndex].id
        // X affects this performance view only; persisted Set data remains untouched.
        temporarilyHiddenMessageIDs.insert(id)
        advanceMessage(1)
    }

    func importLegacyMessageSettings(from url: URL) throws {
        let data = try Data(contentsOf: url)
        let legacy = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        func migratedRows(_ rawRows: [[String: Any]]) -> [MessageRow] {
            var result = rawRows.prefix(10).map { row -> MessageRow in
                let text = String((row["text"] as? String ?? "").prefix(40))
                return MessageRow(text: text,
                                  seconds: max(1, min(3600, row["duration"] as? Int ?? 10)),
                                  global: row["link_all"] as? Bool ?? true,
                                  show: row["show"] as? Bool ?? !text.isEmpty,
                                  image: row["image"] as? String)
            }
            while result.count < 10 { result.append(MessageRow()) }
            return result
        }
        let migrated = migratedRows(legacy["rows"] as? [[String: Any]] ?? [])
        let rawPresets = legacy["presets"] as? [String: Any] ?? [:]
        let saved = mutate("Import legacy message settings") { config in
            func uniqueName(_ proposed: String) -> String {
                let base = proposed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Imported Set" : proposed.trimmingCharacters(in: .whitespacesAndNewlines)
                if !config.messageSets.contains(where: { $0.name.caseInsensitiveCompare(base) == .orderedSame }) { return base }
                var suffix = 2
                while config.messageSets.contains(where: { $0.name.caseInsensitiveCompare("\(base) (Imported \(suffix))") == .orderedSame }) { suffix += 1 }
                return "\(base) (Imported \(suffix))"
            }
            for name in rawPresets.keys.sorted(by: { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }) {
                guard let preset = rawPresets[name] as? [String: Any] else { continue }
                let set = MessageSet(name: uniqueName(name),
                                     rows: migratedRows(preset["rows"] as? [[String: Any]] ?? []),
                                     globalDuration: max(1, min(3600, preset["global_duration"] as? Int ?? 10)))
                config.messageSets.append(set)
            }
            let active = legacy["active_preset"] as? String ?? "Imported"
            let set = MessageSet(name: uniqueName(active), rows: migrated,
                                 globalDuration: max(1, min(3600, legacy["global_duration"] as? Int ?? 10)))
            config.messageSets.append(set)
            config.activeMessageSetID = set.id
            if let seconds = legacy["default_timer_seconds"] as? Int,
               let profileIndex = config.profiles.firstIndex(where: { $0.id == config.activeProfileID }) {
                config.profiles[profileIndex].timerDefaultSeconds = max(60, min(5 * 60 * 60, seconds))
            }
            config.rememberTimerDefault = legacy["remember_timer_default"] as? Bool ?? true
        }
        guard saved else {
            throw NSError(domain: "FloBar.Settings", code: 2, userInfo: [NSLocalizedDescriptionKey: "The imported settings could not be saved; the current configuration was left unchanged."])
        }
    }

    @discardableResult
    func replaceConfigurationFromUserImport(_ imported: FloBarConfiguration) -> Bool {
        let previous = configuration
        if !refusesWritesUntilExplicitImport {
            do { try encodedConfiguration().write(to: preImportBackupURL, options: .atomic) }
            catch {
                lastError = "FloBar could not create a safety backup before import: \(error.localizedDescription)"
                return false
            }
        }
        refusesWritesUntilExplicitImport = false
        configuration = imported
        guard persist() else {
            configuration = previous
            refusesWritesUntilExplicitImport = true
            return false
        }
        startupStorageError = nil
        showSetupWizard = !imported.wizardCompleted
        timerRemaining = TimeInterval(activeProfile.timerDefaultSeconds)
        undoStack.removeAll(); redoStack.removeAll()
        return true
    }

    func startOrPauseTimer() {
        timerRunning.toggle()
        if timerRunning { timerDeadline = ProcessInfo.processInfo.systemUptime + timerRemaining }
        else { updateTimerRemaining() }
        timer?.invalidate()
        guard timerRunning else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updateTimerRemaining() }
        }
    }

    func resetTimer() {
        timer?.invalidate(); timerRunning = false; timerOvertime = false
        timerRemaining = TimeInterval(activeProfile.timerDefaultSeconds)
        timerDeadline = nil
    }

    func saveRememberedTimerDefault() {
        guard configuration.rememberTimerDefault,
              let index = configuration.profiles.firstIndex(where: { $0.id == configuration.activeProfileID }) else { return }
        let seconds = max(60, min(5 * 60 * 60, Int(timerRemaining.rounded())))
        _ = mutate("Remember show timer") { $0.profiles[index].timerDefaultSeconds = seconds }
    }

    private func updateTimerRemaining() {
        guard let timerDeadline else { return }
        let delta = timerDeadline - ProcessInfo.processInfo.systemUptime
        if delta >= 0 { timerRemaining = delta }
        else { timerOvertime = true; timerRemaining = -delta }
    }

    @discardableResult
    func finishWizard() -> Bool {
        let saved = mutate("Finish Setup Wizard") { config in
            config.wizardCompleted = true
            config.wizardCheckpoint = 0
            config.wizardInProgress = false
            config.wizardScope = Array(0..<6)
        }
        if saved { showSetupWizard = false }
        return saved
    }

    func beginWizard(scope: [Int]) {
        _ = mutate("Begin Setup Wizard") { config in
            config.wizardScope = Array(Set(scope.filter { (0..<6).contains($0) })).sorted()
            config.wizardCheckpoint = 0
            config.wizardInProgress = true
        }
    }

    func checkpointWizard(_ step: Int, scope: [Int]) {
        _ = mutate("Setup Wizard Checkpoint") { config in
            config.wizardCheckpoint = max(0, min(scope.count, step))
            config.wizardScope = scope
            config.wizardInProgress = true
        }
    }

    func changeEdge(_ edge: BarEdge) { _ = mutate("Bar Position") { config in
        guard let index = config.profiles.firstIndex(where: { $0.id == config.activeProfileID }) else { return }
        config.profiles[index].edge = edge
    } }
}

extension JSONEncoder {
    static var pretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}
