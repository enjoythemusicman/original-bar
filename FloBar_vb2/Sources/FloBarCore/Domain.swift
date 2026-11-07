import Foundation

public enum BarEdge: String, Codable, CaseIterable, Identifiable {
    case top, bottom, left, right
    public var id: String { rawValue }
    public var isVertical: Bool { self == .left || self == .right }
}

public enum CommandKind: String, Codable, CaseIterable, Hashable { case controlChange, noteOn, hidKey }

public struct MIDICommand: Codable, Equatable, Hashable {
    public var kind: CommandKind
    public var channel: Int
    public var number: Int
    public var value: Int
    public var key: String
    public var displayName: String
    public var shortName: String

    public init(kind: CommandKind = .controlChange, channel: Int = 15, number: Int = 0,
                value: Int = 127, key: String = "", displayName: String = "", shortName: String = "") {
        self.kind = kind; self.channel = channel; self.number = number; self.value = value
        self.key = key; self.displayName = displayName; self.shortName = shortName
    }
}

public struct MessageRow: Codable, Identifiable, Equatable {
    public var id: UUID
    public var text: String
    public var seconds: Int
    public var global: Bool
    public var show: Bool
    public var image: String?

    public init(id: UUID = UUID(), text: String = "", seconds: Int = 10, global: Bool = true, show: Bool = true, image: String? = nil) {
        self.id = id; self.text = text; self.seconds = seconds; self.global = global; self.show = show; self.image = image
    }
}

public struct MessageSet: Codable, Identifiable, Equatable {
    public var id: UUID
    public var name: String
    public var rows: [MessageRow]
    public var globalDuration: Int
    public var readyText: String
    public var readyColor: String

    public init(id: UUID = UUID(), name: String, rows: [MessageRow] = (0..<10).map { _ in MessageRow() },
                globalDuration: Int = 10, readyText: String = "READY", readyColor: String = "#263548") {
        self.id = id; self.name = name; self.rows = Self.uniqueRowIDs(rows); self.globalDuration = globalDuration
        self.readyText = readyText; self.readyColor = readyColor
    }

    private enum CodingKeys: String, CodingKey { case id, name, rows, globalDuration, readyText, readyColor }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "Default"
        rows = Self.uniqueRowIDs(try values.decodeIfPresent([MessageRow].self, forKey: .rows) ?? (0..<10).map { _ in MessageRow() })
        globalDuration = try values.decodeIfPresent(Int.self, forKey: .globalDuration) ?? 10
        readyText = try values.decodeIfPresent(String.self, forKey: .readyText) ?? "READY"
        readyColor = try values.decodeIfPresent(String.self, forKey: .readyColor) ?? "#263548"
    }

    private static func uniqueRowIDs(_ source: [MessageRow]) -> [MessageRow] {
        var seen = Set<UUID>()
        return source.map { original in
            var row = original
            if !seen.insert(row.id).inserted { row.id = UUID(); seen.insert(row.id) }
            return row
        }
    }
}

public struct BarButton: Codable, Identifiable, Equatable {
    public var id: UUID
    public var role: String
    public var title: String
    public var shortTitle: String
    public var command: MIDICommand
    public var inputAliases: [MIDICommand]
    public var activeColor: String
    public var inactiveColor: String
    public var visible: Bool
    public var order: Int

    public init(id: UUID = UUID(), role: String = "custom", title: String, shortTitle: String,
                command: MIDICommand, inputAliases: [MIDICommand] = [], activeColor: String = "#355A80",
                inactiveColor: String = "#263548", visible: Bool = true, order: Int = 0) {
        self.id = id; self.role = role; self.title = title; self.shortTitle = shortTitle; self.command = command
        self.inputAliases = inputAliases; self.activeColor = activeColor; self.inactiveColor = inactiveColor
        self.visible = visible; self.order = order
    }
}

public struct SecondaryStep: Codable, Identifiable, Equatable {
    public enum Kind: String, Codable, CaseIterable { case send, delay, activateJamZone, activateSongBookPro, startWindow, cancel }
    public var id: UUID
    public var kind: Kind
    public var command: MIDICommand
    public var delayMilliseconds: Int
    public var windowSeconds: Double
    public var note: String

    public init(id: UUID = UUID(), kind: Kind = .delay, command: MIDICommand = MIDICommand(),
                delayMilliseconds: Int = 100, windowSeconds: Double = 2, note: String = "") {
        self.id = id; self.kind = kind; self.command = command; self.delayMilliseconds = delayMilliseconds
        self.windowSeconds = windowSeconds; self.note = note
    }
}

public struct SecondaryGroup: Codable, Identifiable, Equatable {
    public var id: UUID
    public var name: String
    public var trigger: MIDICommand
    public var steps: [SecondaryStep]
    public var repeatWindowSeconds: Double
    public var renewWindowOnMatchingCommand: Bool
    public var lockoutSeconds: Double

    public init(id: UUID = UUID(), name: String = "TT1",
                trigger: MIDICommand = MIDICommand(kind: .controlChange, channel: 16, number: 44, value: 127),
                steps: [SecondaryStep] = [], repeatWindowSeconds: Double = 2,
                renewWindowOnMatchingCommand: Bool = true, lockoutSeconds: Double = 2.5) {
        self.id = id; self.name = name; self.trigger = trigger; self.steps = steps
        self.repeatWindowSeconds = repeatWindowSeconds; self.renewWindowOnMatchingCommand = renewWindowOnMatchingCommand
        self.lockoutSeconds = lockoutSeconds
    }
}

/// User-visible timing controls for the approved TT1 state machine. The runtime
/// consumes these values directly; the editor is not merely a reference panel.
public struct TT1Settings: Codable, Equatable {
    public var enabled: Bool
    public var trigger: MIDICommand
    public var specialWindowSeconds: Double
    public var fullFadeSeconds: Double
    public var quickFadeSeconds: Double
    public var quickFadePlayDelaySeconds: Double
    public var downPlayDelaySeconds: Double
    public var splitPlaySettleSeconds: Double
    public var normalPlaySettleSeconds: Double
    public var playToLoopSeconds: Double
    public var loopToUpSeconds: Double
    public var upToRestoreSeconds: Double
    public var sidechainRestoreSeconds: Double
    public var fullVolumeRestoreSeconds: Double
    public var repeatWindowSeconds: Double
    public var renewRepeatWindowOnMatch: Bool
    public var cancelLockoutSeconds: Double
    public var enableG2MBranch: Bool
    public var enableTalkBackBranch: Bool
    public var enableQuickFadeBranch: Bool
    public var enableDownBranch: Bool

    public init(enabled: Bool = true,
                trigger: MIDICommand = MIDICommand(channel: 16, number: 44, value: 127, displayName: "TT1 Trigger", shortName: "TT"),
                specialWindowSeconds: Double = 5, fullFadeSeconds: Double = 15,
                quickFadeSeconds: Double = 1, quickFadePlayDelaySeconds: Double = 1,
                downPlayDelaySeconds: Double = 1.5, splitPlaySettleSeconds: Double = 0.5,
                normalPlaySettleSeconds: Double = 0.05, playToLoopSeconds: Double = 0.5,
                loopToUpSeconds: Double = 0.5, upToRestoreSeconds: Double = 0.5,
                sidechainRestoreSeconds: Double = 7, fullVolumeRestoreSeconds: Double = 30,
                repeatWindowSeconds: Double = 2, renewRepeatWindowOnMatch: Bool = true,
                cancelLockoutSeconds: Double = 2.5, enableG2MBranch: Bool = true,
                enableTalkBackBranch: Bool = true, enableQuickFadeBranch: Bool = true,
                enableDownBranch: Bool = true) {
        self.enabled = enabled; self.trigger = trigger; self.specialWindowSeconds = specialWindowSeconds
        self.fullFadeSeconds = fullFadeSeconds; self.quickFadeSeconds = quickFadeSeconds
        self.quickFadePlayDelaySeconds = quickFadePlayDelaySeconds; self.downPlayDelaySeconds = downPlayDelaySeconds
        self.splitPlaySettleSeconds = splitPlaySettleSeconds; self.normalPlaySettleSeconds = normalPlaySettleSeconds
        self.playToLoopSeconds = playToLoopSeconds; self.loopToUpSeconds = loopToUpSeconds
        self.upToRestoreSeconds = upToRestoreSeconds; self.sidechainRestoreSeconds = sidechainRestoreSeconds
        self.fullVolumeRestoreSeconds = fullVolumeRestoreSeconds; self.repeatWindowSeconds = repeatWindowSeconds
        self.renewRepeatWindowOnMatch = renewRepeatWindowOnMatch; self.cancelLockoutSeconds = cancelLockoutSeconds
        self.enableG2MBranch = enableG2MBranch; self.enableTalkBackBranch = enableTalkBackBranch
        self.enableQuickFadeBranch = enableQuickFadeBranch; self.enableDownBranch = enableDownBranch
    }
}

public struct ControllerProfile: Codable, Equatable {
    public var airName: String
    public var airChannel: Int
    public var smcName: String
    public var smcChannel: Int
    public var p23Name: String
    public var p23Channel: Int

    public init(airName: String = "AIR", airChannel: Int = 16, smcName: String = "SMC", smcChannel: Int = 1,
                p23Name: String = "P23", p23Channel: Int = 15) {
        self.airName = airName; self.airChannel = airChannel; self.smcName = smcName; self.smcChannel = smcChannel
        self.p23Name = p23Name; self.p23Channel = p23Channel
    }
}

public struct BarProfile: Codable, Identifiable, Equatable {
    public var id: UUID
    public var name: String
    public var edge: BarEdge
    public var controllers: ControllerProfile
    public var buttons: [BarButton]
    public var showEffects: Bool
    public var showInputsOutputs: Bool
    public var timerDefaultSeconds: Int
    public var temperatureEnabled: Bool
    public var globalHideShortcutEnabled: Bool
    public var loginItemEnabled: Bool
    public var fxMuteEnabled: Bool
    public var groups: [SecondaryGroup]
    public var tt1: TT1Settings
    public var layoutOrder: [String]
    public var compactSections: Bool
    public var docked: Bool
    public var floatingFrame: PersistedFrame?

    public init(id: UUID = UUID(), name: String = "Default", edge: BarEdge = .top,
                controllers: ControllerProfile = ControllerProfile(), buttons: [BarButton] = BarProfile.defaultButtons,
                showEffects: Bool = true, showInputsOutputs: Bool = true, timerDefaultSeconds: Int = 45 * 60,
                temperatureEnabled: Bool = true, globalHideShortcutEnabled: Bool = false,
                loginItemEnabled: Bool = false, fxMuteEnabled: Bool = true,
                groups: [SecondaryGroup] = [SecondaryGroup(name: "TT1", steps: BarProfile.tt1Template)],
                tt1: TT1Settings = TT1Settings(),
                layoutOrder: [String] = BarProfile.defaultLayoutOrder, compactSections: Bool = false,
                docked: Bool = true, floatingFrame: PersistedFrame? = nil) {
        self.id = id; self.name = name; self.edge = edge; self.controllers = controllers; self.buttons = buttons
        self.showEffects = showEffects; self.showInputsOutputs = showInputsOutputs; self.timerDefaultSeconds = timerDefaultSeconds
        self.temperatureEnabled = temperatureEnabled; self.globalHideShortcutEnabled = globalHideShortcutEnabled
        self.loginItemEnabled = loginItemEnabled; self.fxMuteEnabled = fxMuteEnabled; self.groups = groups; self.tt1 = tt1
        self.layoutOrder = Self.normalizedLayoutOrder(layoutOrder)
        self.compactSections = compactSections; self.docked = docked; self.floatingFrame = floatingFrame
    }

    public static let defaultLayoutOrder = ["message", "fx", "ioSection", "effectsSection", "midiInput", "ready", "timerStack", "bpm"]

    private enum CodingKeys: String, CodingKey {
        case id, name, edge, controllers, buttons, showEffects, showInputsOutputs, timerDefaultSeconds
        case temperatureEnabled, globalHideShortcutEnabled, loginItemEnabled, fxMuteEnabled, groups, tt1
        case layoutOrder, compactSections, docked, floatingFrame
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "Default"
        edge = try values.decodeIfPresent(BarEdge.self, forKey: .edge) ?? .top
        controllers = try values.decodeIfPresent(ControllerProfile.self, forKey: .controllers) ?? ControllerProfile()
        buttons = try values.decodeIfPresent([BarButton].self, forKey: .buttons) ?? Self.defaultButtons
        showEffects = try values.decodeIfPresent(Bool.self, forKey: .showEffects) ?? true
        showInputsOutputs = try values.decodeIfPresent(Bool.self, forKey: .showInputsOutputs) ?? true
        timerDefaultSeconds = try values.decodeIfPresent(Int.self, forKey: .timerDefaultSeconds) ?? 45 * 60
        temperatureEnabled = try values.decodeIfPresent(Bool.self, forKey: .temperatureEnabled) ?? true
        globalHideShortcutEnabled = try values.decodeIfPresent(Bool.self, forKey: .globalHideShortcutEnabled) ?? false
        loginItemEnabled = try values.decodeIfPresent(Bool.self, forKey: .loginItemEnabled) ?? false
        fxMuteEnabled = try values.decodeIfPresent(Bool.self, forKey: .fxMuteEnabled) ?? true
        groups = try values.decodeIfPresent([SecondaryGroup].self, forKey: .groups) ?? [SecondaryGroup(name: "TT1", steps: Self.tt1Template)]
        tt1 = try values.decodeIfPresent(TT1Settings.self, forKey: .tt1) ?? TT1Settings()
        layoutOrder = Self.normalizedLayoutOrder(try values.decodeIfPresent([String].self, forKey: .layoutOrder) ?? Self.defaultLayoutOrder)
        compactSections = try values.decodeIfPresent(Bool.self, forKey: .compactSections) ?? false
        docked = try values.decodeIfPresent(Bool.self, forKey: .docked) ?? true
        floatingFrame = try values.decodeIfPresent(PersistedFrame.self, forKey: .floatingFrame)
    }

    private static func normalizedLayoutOrder(_ proposed: [String]) -> [String] {
        let valid = Set(defaultLayoutOrder)
        var result = proposed.filter { valid.contains($0) }
        for item in defaultLayoutOrder where !result.contains(item) { result.append(item) }
        return result
    }

    public static let defaultButtons: [BarButton] = [
        .init(role: "fxMute", title: "FX Global Mute", shortTitle: "FX", command: .init(kind: .noteOn, channel: 16, number: 22, value: 127, displayName: "FX Global Mute", shortName: "fxM"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 22, value: 127, displayName: "SMC FX Global Mute", shortName: "fxM")], activeColor: "#B75B62", inactiveColor: "#355A80", order: -1),
        .init(role: "g2m", title: "Guitar to Monitor", shortTitle: "G2M", command: .init(channel: 16, number: 42, displayName: "Guitar to Monitor", shortName: "G2M"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 2, value: 127, displayName: "SMC Guitar to Monitor", shortName: "G2M")], order: 0),
        .init(role: "talkBack", title: "TalkBack", shortTitle: "TB", command: .init(channel: 16, number: 45, displayName: "TalkBack", shortName: "TB"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 0, value: 127, displayName: "SMC TalkBack", shortName: "TB")], order: 1),
        .init(role: "monitorMute", title: "Monitor Mute", shortTitle: "MM", command: .init(channel: 16, number: 41, displayName: "Monitor Mute", shortName: "MM"), order: 2),
        .init(role: "bvox", title: "Bvox", shortTitle: "Bvox", command: .init(channel: 16, number: 39, displayName: "Bvox", shortName: "Bvox"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 3, value: 127, displayName: "SMC Bvox", shortName: "Bvox")], order: 3),
        .init(role: "solos", title: "Solos", shortTitle: "Solos", command: .init(channel: 16, number: 40, displayName: "Solos", shortName: "Solos"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 11, value: 127, displayName: "SMC Solos", shortName: "Solos")], order: 4),
        .init(role: "sidechain", title: "FX Sidechain", shortTitle: "fxSC", command: .init(channel: 16, number: 43, displayName: "FX Sidechain", shortName: "fxSC"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 14, value: 127, displayName: "SMC FX State", shortName: "fxSC")], activeColor: "#42A879", order: 5),
        .init(role: "delay", title: "Delay", shortTitle: "DLY", command: .init(channel: 16, number: -1, displayName: "Delay", shortName: "DLY"), order: 6),
        .init(role: "chorus", title: "Chorus", shortTitle: "CHR", command: .init(channel: 16, number: -1, displayName: "Chorus", shortName: "CHR"), order: 7),
        .init(role: "distortion", title: "Distortion", shortTitle: "DIS", command: .init(channel: 16, number: -1, displayName: "Distortion", shortName: "DIS"), order: 8),
        .init(role: "overdrive", title: "Overdrive", shortTitle: "O.D.", command: .init(channel: 16, number: -1, displayName: "Overdrive", shortName: "O.D."), order: 9),
        .init(role: "flanger", title: "Flanger", shortTitle: "FLG", command: .init(channel: 16, number: -1, displayName: "Flanger", shortName: "FLG"), order: 10),
        .init(role: "wah", title: "Wah", shortTitle: "WHA", command: .init(channel: 16, number: -1, displayName: "Wah", shortName: "WHA"), order: 11),
        .init(role: "infrared", title: "Infrared", shortTitle: "IR", command: .init(channel: 16, number: -1, displayName: "Infrared", shortName: "IR"), order: 12),
        .init(role: "play", title: "JamZone Play", shortTitle: "P", command: .init(channel: 16, number: 22, displayName: "JamZone Play", shortName: "P"), visible: false, order: 13),
        .init(role: "tapTempo", title: "Tap Tempo", shortTitle: "TT", command: .init(channel: 16, number: 44, displayName: "Tap Tempo", shortName: "TT"), visible: false, order: 14),
        .init(role: "loop", title: "Loop / AutoScroll", shortTitle: "L", command: .init(channel: 16, number: 100, displayName: "Loop", shortName: "L"), visible: false, order: 15),
        .init(role: "goBack", title: "Go Back", shortTitle: "GBk", command: .init(channel: 16, number: 101, displayName: "Go Back", shortName: "GBk"), visible: false, order: 16),
        .init(role: "guitarMute", title: "Guitar Mute", shortTitle: "gtM", command: .init(kind: .noteOn, channel: 16, number: 18, value: 127, displayName: "Guitar Mute", shortName: "gtM"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 18, value: 127, displayName: "SMC Guitar Mute", shortName: "gtM")], visible: false, order: 17),
        .init(role: "hide", title: "FloBar Hide", shortTitle: "fbH", command: .init(kind: .noteOn, channel: 16, number: 93, value: 127, displayName: "FloBar Hide", shortName: "fbH"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 93, value: 127, displayName: "SMC Hide", shortName: "fbH")], visible: false, order: 18),
        .init(role: "split", title: "JamZone/SBP Flip", shortTitle: "j/sF", command: .init(kind: .noteOn, channel: 16, number: 94, value: 127, displayName: "JamZone/SBP Flip", shortName: "j/sF"), inputAliases: [.init(kind: .noteOn, channel: 1, number: 94, value: 127, displayName: "SMC Split", shortName: "j/sF")], visible: false, order: 19)
    ]

    // The template remains a reference until the user explicitly edits and saves a sequence.
    public static let tt1Template: [SecondaryStep] = [
        .init(kind: .send, command: .init(kind: .controlChange, channel: 15, number: 20, value: 127, displayName: "Full Fade Start", shortName: "CC20"), note: "Start full fade"),
        .init(kind: .startWindow, windowSeconds: 5, note: "G2M / TB special window"),
        .init(kind: .delay, delayMilliseconds: 5000, note: "Wait before full fade"),
        .init(kind: .send, command: .init(kind: .controlChange, channel: 15, number: 43, value: 127, displayName: "FX Sidechain Bypass", shortName: "fxSC"), note: "Quick-fade branch only: start bypass"),
        .init(kind: .delay, delayMilliseconds: 7000, note: "Quick-fade branch only"),
        .init(kind: .send, command: .init(kind: .controlChange, channel: 15, number: 43, value: 0, displayName: "FX Sidechain Restore", shortName: "fxSC"), note: "Quick-fade branch only: restore")
    ]
}

public struct PersistedFrame: Codable, Equatable {
    public var x: Double
    public var y: Double
    public var width: Double
    public var height: Double
    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x; self.y = y; self.width = width; self.height = height
    }
}

public struct FloBarConfiguration: Codable, Equatable {
    public var schemaVersion: Int
    public var profiles: [BarProfile]
    public var activeProfileID: UUID
    public var messageSets: [MessageSet]
    public var activeMessageSetID: UUID
    public var wizardCompleted: Bool
    public var wizardCheckpoint: Int
    public var wizardInProgress: Bool
    public var wizardScope: [Int]
    public var startOnLogin: Bool
    public var rememberTimerDefault: Bool

    public init() {
        let profile = BarProfile()
        let messageSet = MessageSet(name: "Default")
        schemaVersion = 1; profiles = [profile]; activeProfileID = profile.id
        messageSets = [messageSet]; activeMessageSetID = messageSet.id
        wizardCompleted = false; wizardCheckpoint = 0; wizardInProgress = false; wizardScope = Array(0..<6)
        startOnLogin = false; rememberTimerDefault = true
    }
}

public enum MIDIEvent: Equatable {
    case cc(channel: Int, number: Int, value: Int)
    case noteOn(channel: Int, number: Int, velocity: Int)
    case noteOff(channel: Int, number: Int)
    case hid(key: String)

    public var channel: Int? {
        switch self {
        case let .cc(ch, _, _), let .noteOn(ch, _, _), let .noteOff(ch, _): return ch
        case .hid: return nil
        }
    }
    public var rawLabel: String {
        switch self {
        case let .cc(_, cc, value): return "cc\(cc)v\(value)"
        case let .noteOn(_, note, velocity): return "n\(note)v\(velocity)"
        case let .noteOff(_, note): return "n\(note)v0"
        case let .hid(key): return key == "Up" ? "↑" : key == "Down" ? "↓" : key
        }
    }
}

public enum ControllerKind: String, Hashable { case air = "AIR", smc = "SMC", p23 = "P23", iac = "IAC", unknown = "" }

public struct StartupSyncEvent: Equatable {
    public var kind: CommandKind
    public var number: Int
    public var value: Int
    public var channel: Int
    public var label: String
    public init(kind: CommandKind, number: Int, value: Int, channel: Int, label: String) {
        self.kind = kind; self.number = number; self.value = value; self.channel = channel; self.label = label
    }
    public static let approvedPass: [StartupSyncEvent] = [
        .init(kind: .controlChange, number: 42, value: 127, channel: 15, label: "G2M"),
        .init(kind: .controlChange, number: 45, value: 127, channel: 15, label: "TB"),
        .init(kind: .controlChange, number: 41, value: 127, channel: 15, label: "MM"),
        .init(kind: .controlChange, number: 39, value: 127, channel: 15, label: "Bvox"),
        .init(kind: .controlChange, number: 40, value: 127, channel: 15, label: "Solos"),
        .init(kind: .noteOn, number: 22, value: 127, channel: 15, label: "FX Mute"),
        .init(kind: .noteOn, number: 18, value: 127, channel: 15, label: "Guitar Mute"),
        .init(kind: .controlChange, number: 43, value: 127, channel: 15, label: "FX Sidechain")
    ]
    public static var approvedSequence: [StartupSyncEvent] { approvedPass + approvedPass }
}

public struct ControllerPresentation: Equatable {
    public var connected: Bool
    public var battery: Int?
    public var batteryIsReliable: Bool
    public var commandName: String?
    public init(connected: Bool = false, battery: Int? = nil, batteryIsReliable: Bool = false, commandName: String? = nil) {
        self.connected = connected; self.battery = battery; self.batteryIsReliable = batteryIsReliable; self.commandName = commandName
    }
}

public func validMessageText(_ text: String) -> Bool { text.count <= 40 }

public func primaryLanguageDirection(_ text: String) -> Bool {
    for scalar in text.unicodeScalars {
        if (0x0590...0x08FF).contains(Int(scalar.value)) { return true }
        if CharacterSet.letters.contains(scalar) { return false }
    }
    return false
}
