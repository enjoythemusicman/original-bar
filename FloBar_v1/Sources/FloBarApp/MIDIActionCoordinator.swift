import AppKit
import Foundation
import FloBarCore

private final class ActivationState {
    private let lock = NSLock()
    private var stored: (ExternalActions.FocusResult?, TimeInterval?) = (nil, nil)
    func set(result: ExternalActions.FocusResult, completedAt: TimeInterval) {
        lock.lock(); stored = (result, completedAt); lock.unlock()
    }
    var value: (result: ExternalActions.FocusResult?, completedAt: TimeInterval?) {
        lock.lock(); defer { lock.unlock() }
        return (stored.0, stored.1)
    }
}

final class MIDIActionCoordinator {
    private let queue = DispatchQueue(label: "FloBar.MIDI.ActionSequencer", qos: .userInitiated)
    private let midi: CoreMIDIService
    private let external = ExternalActions()
    private weak var model: FloBarModel?
    private(set) var splitLayout: Bool
    private var activeProfile: BarProfile
    private var fullFadeActive = false
    private var tt1ScenarioActive = false
    private var quickFadeActive = false
    private var fullFadeStartedAt: TimeInterval?
    private var specialUntil: TimeInterval = 0
    private var repeatKind: Int? // CC42 or CC45
    private var repeatGeneration = 0
    private var downSequenceActive = false
    private var generation = 0
    private var lockoutUntil: TimeInterval = 0
    private var lastControllerSourceText: String?
    private var lastControllerSourceAt: TimeInterval = 0
    var onHide: (() -> Void)?
    var onShow: (() -> Void)?
    var onRestart: (() -> Void)?
    var onLayoutChanged: ((Bool) -> Void)?

    init(midi: CoreMIDIService, model: FloBarModel, initialProfile: BarProfile) {
        self.midi = midi
        self.model = model
        self.activeProfile = initialProfile
        // v99 starts in the split/compact layout; keep that approved default.
        self.splitLayout = true
    }

    func updateProfile(_ profile: BarProfile) { queue.async { [weak self] in self?.activeProfile = profile } }

    func recordOutput(_ event: MIDIEvent, origin: MIDIOutputOrigin) {
        guard origin == .controllerAction else { return }
        queue.async { [weak self] in
            guard let self, let sourceText = self.lastControllerSourceText,
                  ProcessInfo.processInfo.systemUptime - self.lastControllerSourceAt <= 30 else { return }
            Task { @MainActor [weak model = self.model] in model?.recordPairedIACOutput(event, sourceText: sourceText) }
        }
    }

    func receive(_ event: MIDIEvent, source: String) {
        Task { @MainActor [weak model] in model?.receive(event, from: source) }
        queue.async { [weak self] in self?.process(event, source: source) }
    }

    private func process(_ event: MIDIEvent, source: String) {
        let sourceLower = source.lowercased()
        if sourceLower.contains("iac driver bus 2") || sourceLower.contains("iac bus 2") {
            updateConfirmedState(event)
            return
        }
        let isControllerInput = sourceLower.contains("airstep") || sourceLower.contains("smc") || sourceLower.contains("p23")
        let isGlobalHID = sourceLower == "global hid"
        guard isControllerInput || isGlobalHID else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if isControllerInput { lastControllerSourceText = event.rawLabel; lastControllerSourceAt = now }
        if now < lockoutUntil {
            if matches(event, command: activeProfile.tt1.trigger) {
                sendActionCC(channel: 15, number: 44, value: event.commandValue ?? 127)
                lockoutUntil = now + activeProfile.tt1.cancelLockoutSeconds
            }
            return
        }

        if matches(event, role: "hide") {
            DispatchQueue.main.async { [weak self] in self?.toggleVisibility() }; return
        }
        if matches(event, role: "split") { toggleSplitLayout(); return }
        if case let .hid(key) = event {
            if key == "Down", tt1ScenarioActive, fullFadeActive, !quickFadeActive, activeProfile.tt1.enableDownBranch { startQuickFade(isDown: true) }
            return
        }

        if activeProfile.tt1.enabled, matches(event, command: activeProfile.tt1.trigger) {
            repeatKind = nil
            repeatGeneration += 1
            if tt1ScenarioActive {
                sendActionCC(channel: 15, number: 44, value: event.commandValue ?? 127)
                cancelCurrent(startLockout: true)
            }
            else { startFullFade() }
            return
        }
        if repeatKind != nil {
            if repeatKind == 42, activeProfile.tt1.enableG2MBranch, matches(event, role: "g2m") { forwardG2M(event.commandValue ?? 127); return }
            if repeatKind == 45, activeProfile.tt1.enableTalkBackBranch, matches(event, role: "talkBack") { forwardTalkBack(event.commandValue ?? 127); return }
            return
        }
        if fullFadeActive, activeProfile.tt1.enableG2MBranch, now <= specialUntil, matches(event, role: "g2m") { cancelFullFadeForSpecialBranch(); forwardG2M(event.commandValue ?? 127); return }
        if fullFadeActive, activeProfile.tt1.enableTalkBackBranch, now <= specialUntil, matches(event, role: "talkBack") { cancelFullFadeForSpecialBranch(); forwardTalkBack(event.commandValue ?? 127); return }
        if case let .cc(_, _, playValue) = event, tt1ScenarioActive, matches(event, role: "play"), playValue == 127 {
            if activeProfile.tt1.enableQuickFadeBranch && !quickFadeActive { startQuickFade(isDown: false) }
            return
        }
        if case let .cc(_, _, playValue) = event, matches(event, role: "play"), playValue == 127 { playIndependent(); return }
        if case .cc = event, matches(event, role: "loop") { handleLoopCommand(source: source); return }
        if case .cc = event, matches(event, role: "goBack"), !splitLayout { _ = external.optionLeftIfJamZoneFront(); return }
    }

    private func mappedCommands(role: String) -> [MIDICommand] {
        guard let button = activeProfile.buttons.first(where: { $0.role == role }) else { return [] }
        return [button.command] + button.inputAliases
    }
    private func matches(_ event: MIDIEvent, role: String) -> Bool {
        mappedCommands(role: role).contains { matches(event, command: $0) }
    }
    private func matches(_ event: MIDIEvent, trigger: MIDICommand?) -> Bool {
        guard let trigger else { return false }
        return matches(event, command: trigger)
    }
    private func matches(_ event: MIDIEvent, command: MIDICommand) -> Bool {
        switch (event, command.kind) {
        case let (.cc(ch, number, _), .controlChange): return command.number >= 0 && ch == command.channel && number == command.number
        case let (.noteOn(ch, number, _), .noteOn): return command.number >= 0 && ch == command.channel && number == command.number
        case let (.hid(key), .hidKey): return key.caseInsensitiveCompare(command.key) == .orderedSame
        default: return false
        }
    }

    private func updateConfirmedState(_ event: MIDIEvent) {
        let colorKey: Int?
        let stateOn: Bool
        switch event {
        case let .cc(channel, cc, value) where channel == 15:
            colorKey = activeProfile.buttons.first(where: { $0.command.kind == .controlChange && $0.command.number == cc })?.command.number
            stateOn = value != 0
        case let .noteOn(channel, note, velocity) where channel == 15:
            colorKey = activeProfile.buttons.first(where: { button in
                button.command.kind == .noteOn && button.command.number == note || button.inputAliases.contains { $0.kind == .noteOn && $0.number == note }
            })?.command.number
            stateOn = velocity != 0
        case let .noteOff(channel, note) where channel == 15:
            colorKey = activeProfile.buttons.first(where: { button in
                button.command.kind == .noteOn && button.command.number == note || button.inputAliases.contains { $0.kind == .noteOn && $0.number == note }
            })?.command.number
            stateOn = false
        default: colorKey = nil; stateOn = false
        }
        guard let colorKey else { return }
        Task { @MainActor [weak model] in model?.stateColors[colorKey] = stateOn ? "on" : "off" }
    }

    private func toggleVisibility() { onHide?() }
    func manualRestart() { DispatchQueue.main.async { [weak self] in self?.onRestart?() } }
    func toggleVisibilityFromShortcut() { DispatchQueue.main.async { [weak self] in self?.toggleVisibility() } }

    func activateBarButton(_ button: BarButton) {
        queue.async { [weak self] in
            guard let self else { return }
            switch button.role {
            case "play": self.playIndependent()
            case "loop": self.handleLoopCommand(source: "FloBar Bar")
            case "goBack":
                if !self.splitLayout { _ = self.external.optionLeftIfJamZoneFront() }
            case "hide": DispatchQueue.main.async { self.toggleVisibility() }
            case "split": self.toggleSplitLayout()
            default:
                guard button.command.number >= 0 else { return }
                switch button.command.kind {
                case .controlChange:
                    self.midi.sendCC(channel: 15, number: button.command.number, value: button.command.value, origin: .localButton)
                case .noteOn:
                    self.midi.sendNote(channel: 15, number: button.command.number, velocity: button.command.value, origin: .localButton)
                case .hidKey: break
                }
            }
        }
    }

    private func cancelFullFadeForSpecialBranch() {
        generation += 1
        fullFadeActive = false
        tt1ScenarioActive = false
        fullFadeStartedAt = nil
    }

    private func startFullFade() {
        generation += 1
        let token = generation
        fullFadeActive = true
        tt1ScenarioActive = true
        fullFadeStartedAt = ProcessInfo.processInfo.systemUptime
        specialUntil = ProcessInfo.processInfo.systemUptime + activeProfile.tt1.specialWindowSeconds
        repeatKind = nil
        sendActionCC(channel: 15, number: 20, value: 127)
        schedule(after: activeProfile.tt1.specialWindowSeconds, token: token) { [weak self] in self?.runFullFade(token: token) }
    }

    private func runFullFade(token: Int) {
        guard token == generation, fullFadeActive else { return }
        let fadeDuration = activeProfile.tt1.fullFadeSeconds
        for step in 1...127 {
            queue.asyncAfter(deadline: .now() + .nanoseconds(Int((Double(step) * fadeDuration / 127.0) * 1_000_000_000))) { [weak self] in
                guard let self, self.generation == token, self.fullFadeActive else { return }
                self.sendActionCC(channel: 15, number: 20, value: 127 - step)
                if step == 127 {
                    self.fullFadeActive = false
                    self.tt1ScenarioActive = false
                    self.activateJamZone(playAfter: 0, returnToSBP: false) { [weak self] in
                        guard let self, self.generation == token else { return }
                        self.sendActionCC(channel: 15, number: 20, value: 127)
                    }
                }
            }
        }
    }

    private func startQuickFade(isDown: Bool) {
        generation += 1
        let token = generation
        let tt1 = activeProfile.tt1
        let shouldRestoreFull = fullFadeStartedAt.map { ProcessInfo.processInfo.systemUptime <= $0 + tt1.specialWindowSeconds + tt1.fullFadeSeconds } ?? false
        let startedAt = ProcessInfo.processInfo.systemUptime
        quickFadeActive = true
        downSequenceActive = isDown
        sendActionCC(channel: 15, number: 43, value: 127)
        schedule(after: tt1.sidechainRestoreSeconds, token: nil) { [weak self] in self?.sendActionCC(channel: 15, number: 43, value: 0) }
        let activation = ActivationState()
        DispatchQueue.global(qos: .userInitiated).async { [external] in
            activation.set(result: external.activateRunningApp(named: "Jamzone"), completedAt: ProcessInfo.processInfo.systemUptime)
        }
        // Non-blocking, evenly spaced absolute CC21 values over the configured duration.
        for step in 1...127 {
            queue.asyncAfter(deadline: .now() + .nanoseconds(Int(Double(step) / 127.0 * tt1.quickFadeSeconds * 1_000_000_000))) { [weak self] in
                guard let self, self.generation == token else { return }
                self.sendActionCC(channel: 15, number: 21, value: 127 - step)
                if step == 127 {
                    self.completeQuickFade(token: token, isDown: isDown, startedAt: startedAt,
                                           pTarget: startedAt + (isDown ? tt1.downPlayDelaySeconds : tt1.quickFadePlayDelaySeconds), activation: activation,
                                           restoreFull: shouldRestoreFull)
                }
            }
        }
        fullFadeActive = false
    }

    private func completeQuickFade(token: Int, isDown: Bool, startedAt: TimeInterval, pTarget: TimeInterval,
                                   activation: ActivationState, restoreFull: Bool) {
        guard generation == token else { return }
        let tt1 = activeProfile.tt1
        let focus = activation.value
        if focus.result == nil {
            guard ProcessInfo.processInfo.systemUptime - startedAt < 5 else {
                Task { @MainActor [weak model] in model?.log("Quick-fade playback skipped because Jamzone did not confirm foreground activation within 5s.") }
                sendActionCC(channel: 15, number: 21, value: 127)
                downSequenceActive = false
                tt1ScenarioActive = false
                quickFadeActive = false
                return
            }
            queue.asyncAfter(deadline: .now() + .milliseconds(20)) { [weak self] in
                self?.completeQuickFade(token: token, isDown: isDown, startedAt: startedAt,
                                        pTarget: pTarget, activation: activation, restoreFull: restoreFull)
            }
            return
        }
        guard focus.result == .focused, let focusCompletedAt = focus.completedAt else {
            Task { @MainActor [weak model] in model?.log("Quick-fade playback skipped because Jamzone could not be confirmed in front.") }
            sendActionCC(channel: 15, number: 21, value: 127)
            downSequenceActive = false
            tt1ScenarioActive = false
            quickFadeActive = false
            return
        }
        let pAt = max(pTarget, focusCompletedAt + (splitLayout ? tt1.splitPlaySettleSeconds : tt1.normalPlaySettleSeconds))
        let pToLoop = tt1.playToLoopSeconds
        let loopToUp = tt1.loopToUpSeconds
        let upToRestore = tt1.upToRestoreSeconds
        queue.asyncAfter(deadline: .now() + max(0, pAt - ProcessInfo.processInfo.systemUptime)) { [weak self] in
            guard let self, self.generation == token else { return }
            self.external.sendKeystrokeAsync("p", toProcess: "Jamzone") { [weak self] sent in
                guard let self else { return }
                self.queue.async {
                    guard self.generation == token else { return }
                    guard sent else { self.finishQuickFade(token: token, restoreFull: restoreFull); return }
                    self.queue.asyncAfter(deadline: .now() + pToLoop) {
                        guard self.generation == token else { return }
                        guard isDown else { self.finishQuickFade(token: token, restoreFull: restoreFull); return }
                        self.external.sendKeystrokeAsync("l", toProcess: "Jamzone") { [weak coordinator = self] loopSent in
                            guard let coordinator else { return }
                            coordinator.queue.async {
                                guard coordinator.generation == token else { return }
                                guard loopSent else { coordinator.finishQuickFade(token: token, restoreFull: restoreFull); return }
                                coordinator.queue.asyncAfter(deadline: .now() + loopToUp) {
                                    guard coordinator.generation == token else { return }
                                    _ = coordinator.external.sendArrowUpToFrontmostApp()
                                    coordinator.queue.asyncAfter(deadline: .now() + upToRestore) {
                                        coordinator.finishQuickFade(token: token, restoreFull: restoreFull)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _ = startedAt
    }

    private func finishQuickFade(token: Int, restoreFull: Bool) {
        guard generation == token else { return }
        let fullRestoreDelay = activeProfile.tt1.fullVolumeRestoreSeconds
        sendActionCC(channel: 15, number: 21, value: 127)
        tt1ScenarioActive = false
        quickFadeActive = false
        if splitLayout {
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                guard let self else { return }
                let result = self.external.activateRunningApp(named: "SongBookPro")
                self.queue.async { [weak self] in
                    guard let self, self.generation == token else { return }
                    if result != .focused { Task { @MainActor [weak model = self.model] in model?.log("Could not return SongBookPro to the foreground after quick fade.") } }
                    self.downSequenceActive = false
                    if restoreFull { self.queue.asyncAfter(deadline: .now() + fullRestoreDelay) { [weak self] in self?.sendActionCC(channel: 15, number: 20, value: 127) } }
                }
            }
        } else {
            downSequenceActive = false
            if restoreFull { queue.asyncAfter(deadline: .now() + fullRestoreDelay) { [weak self] in self?.sendActionCC(channel: 15, number: 20, value: 127) } }
        }
    }

    private func playIndependent() {
        let split = splitLayout
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let focused = self.external.activateRunningApp(named: "Jamzone")
            guard focused == .focused else { return }
            let wait = split ? self.activeProfile.tt1.splitPlaySettleSeconds : self.activeProfile.tt1.normalPlaySettleSeconds
            self.queue.asyncAfter(deadline: .now() + .milliseconds(Int(wait * 1000))) { [weak self] in
                guard let self else { return }
                self.external.sendKeystrokeAsync("p", toProcess: "Jamzone") { [weak self] sent in
                    guard let self, split, sent else { return }
                    self.queue.asyncAfter(deadline: .now() + .milliseconds(300)) {
                        DispatchQueue.global(qos: .userInitiated).async { _ = self.external.activateRunningApp(named: "SongBookPro") }
                    }
                }
            }
        }
    }

    private func activateJamZone(playAfter delay: TimeInterval, returnToSBP: Bool, completion: @escaping () -> Void) {
        let split = returnToSBP
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            guard self.external.activateRunningApp(named: "Jamzone") == .focused else {
                Task { @MainActor [weak model = self.model] in model?.log("Could not bring Jamzone to the foreground; P was not sent.") }
                self.queue.async { completion() }
                return
            }
            self.queue.asyncAfter(deadline: .now() + .milliseconds(Int(max(delay, split ? 0.3 : 0.05) * 1000))) { [weak self] in
                guard let self else { return }
                self.external.sendKeystrokeAsync("p", toProcess: "Jamzone") { [weak self] sent in
                    guard let self else { return }
                    self.queue.asyncAfter(deadline: .now() + (sent ? 0.5 : 0)) {
                        if split {
                            DispatchQueue.global(qos: .userInitiated).async { [weak coordinator = self] in
                                guard let coordinator else { return }
                                _ = coordinator.external.activateRunningApp(named: "SongBookPro")
                                coordinator.queue.async { completion() }
                            }
                        } else { completion() }
                    }
                }
            }
        }
    }

    private func handleLoopCommand(source: String) {
        let sourceKind = source.lowercased().contains("smc") ? ControllerKind.smc : source.lowercased().contains("p23") ? ControllerKind.p23 : ControllerKind.air
        if !downSequenceActive, splitLayout, NSWorkspace.shared.frontmostApplication?.localizedName?.localizedCaseInsensitiveContains("SongBookPro") == true {
            external.sendKeystrokeAsync("l", toProcess: "SongBookPro") { _ in }
            Task { @MainActor [weak model] in model?.controllerLines[sourceKind] = ".A.S"; model?.lastCommandController = sourceKind; model?.lastCommandName = ".A.S" }
        } else {
            external.sendKeystrokeAsync("l", toProcess: "Jamzone") { _ in }
        }
    }

    private func forwardG2M(_ value: Int) {
        sendActionCC(channel: 15, number: 42, value: value)
        external.switchApplicationsAsync()
        startRepeatWindow(42)
    }

    private func forwardTalkBack(_ value: Int) {
        sendActionCC(channel: 15, number: 45, value: value)
        toggleSplitLayout()
        startRepeatWindow(45)
    }

    private func startRepeatWindow(_ kind: Int) {
        if repeatKind == kind && !activeProfile.tt1.renewRepeatWindowOnMatch { return }
        repeatKind = kind
        repeatGeneration += 1
        let token = repeatGeneration
        let duration = activeProfile.tt1.repeatWindowSeconds
        schedule(after: duration, token: nil) { [weak self] in
            guard let self, token == self.repeatGeneration else { return }
            self.repeatKind = nil
            self.fullFadeActive = false
        }
    }

    private func toggleSplitLayout() {
        splitLayout.toggle()
        onLayoutChanged?(splitLayout)
        let isSplit = splitLayout
        let screen = NSScreen.main?.frame ?? .zero
        DispatchQueue.global(qos: .userInitiated).async { [external] in
            _ = external.setJamZoneLayout(split: isSplit, screenWidth: Int(screen.width), screenHeight: Int(screen.height))
            _ = external.activateRunningApp(named: isSplit ? "SongBookPro" : "Jamzone")
        }
    }

    private func cancelCurrent(startLockout: Bool) {
        generation += 1
        fullFadeActive = false
        tt1ScenarioActive = false
        quickFadeActive = false
        repeatKind = nil
        downSequenceActive = false
        fullFadeStartedAt = nil
        sendActionCC(channel: 15, number: 21, value: 127)
        if startLockout { lockoutUntil = ProcessInfo.processInfo.systemUptime + activeProfile.tt1.cancelLockoutSeconds }
    }

    private func sendActionCC(channel: Int, number: Int, value: Int) {
        midi.sendCC(channel: channel, number: number, value: value, origin: .controllerAction)
    }

    private func schedule(after seconds: TimeInterval, token: Int?, _ action: @escaping () -> Void) {
        queue.asyncAfter(deadline: .now() + seconds) { [weak self] in
            guard let self, token == nil || self.generation == token else { return }
            action()
        }
    }
}

private extension MIDIEvent {
    var commandValue: Int? {
        switch self {
        case let .cc(_, _, value): return value
        case let .noteOn(_, _, velocity): return velocity
        case .noteOff: return 0
        case .hid: return nil
        }
    }
}
