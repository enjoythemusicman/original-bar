import AppKit
import AppKit
import FloBarCore
import SwiftUI

struct SettingsRoot: View {
    @ObservedObject var model: FloBarModel
    let app: FloBarApplication
    let startsOnWizard: Bool
    @State private var selection = "Messages"

    var body: some View {
        TabView(selection: $selection) {
            SetupWizardView(model: model, app: app) { selection = "Actions" }.tabItem { Label("Setup", systemImage: "wand.and.stars") }.tag("Setup")
            ControlSettingsView(model: model).tabItem { Label("Bar & MIDI", systemImage: "slider.horizontal.3") }.tag("Bar & MIDI")
            MessageSetSettingsView(model: model).tabItem { Label("Message Sets", systemImage: "text.bubble") }.tag("Messages")
            SecondaryActionSettingsView(model: model).tabItem { Label("Actions", systemImage: "point.topleft.down.curvedto.point.bottomright.up") }.tag("Actions")
            PrivacyBackupSettingsView(model: model, app: app).tabItem { Label("Data & Access", systemImage: "lock.shield") }.tag("Data")
        }
        .padding(12)
        .frame(minWidth: 820, minHeight: 590)
        .onAppear { if startsOnWizard { selection = "Setup" } }
    }
}

struct SetupWizardView: View {
    @ObservedObject var model: FloBarModel
    let app: FloBarApplication
    let onOpenActions: () -> Void
    @State private var step = 0
    @State private var runScope = Array(0..<6)
    @State private var selectedScope = Set(0..<6)
    @State private var scopeChosen = false
    @State private var finishedInView = false
    @State private var firstSetupRun = false
    @State private var profile = BarProfile()
    @State private var firstMessage = ""
    @State private var secondMessage = ""
    @State private var offerActions = false
    @State private var learningButtonID: UUID?

    private let titles = ["MIDI Controllers", "Guitar Effects", "Inputs & Outputs", "Show Timer", "Message Box", "FX & Sidechain"]

    private var activeStep: Int { runScope[min(max(step, 0), max(0, runScope.count - 1))] }
    private var choosingScope: Bool { model.configuration.wizardCompleted && !scopeChosen && !finishedInView }

    var body: some View {
        Group {
            if choosingScope { scopeChooser }
            else { wizardEditor }
        }
        .onChange(of: model.midiEventGeneration) { _ in
            guard let learningButtonID,
                  let event = model.lastReceivedEvent,
                  !model.lastReceivedSource.localizedCaseInsensitiveContains("iac"),
                  let index = profile.buttons.firstIndex(where: { $0.id == learningButtonID }) else { return }
            applyLearned(event, source: model.lastReceivedSource, to: index)
            self.learningButtonID = nil
        }
        .onAppear {
            profile = model.activeProfile
            firstSetupRun = !model.configuration.wizardCompleted
            if model.configuration.wizardInProgress, !model.configuration.wizardScope.isEmpty {
                runScope = model.configuration.wizardScope
                step = min(model.configuration.wizardCheckpoint, runScope.count - 1)
                scopeChosen = true
            } else if !model.configuration.wizardCompleted {
                runScope = Array(0..<6)
                step = 0
                scopeChosen = true
                model.beginWizard(scope: runScope)
            }
        }
        .alert("Setup complete", isPresented: $offerActions) {
            Button("Build secondary actions now") { onOpenActions() }
            Button("Later in Settings", role: .cancel) { }
        } message: { Text("You can now configure the TT1-style Secondary Action Builder, or open it later from Settings → Actions.") }
    }

    private var wizardEditor: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("FloBar Setup").font(.system(size: 25, weight: .bold, design: .rounded))
                Spacer()
                Text("Step \(step + 1) of \(runScope.count)").foregroundStyle(.secondary)
            }
            ProgressView(value: Double(step + 1), total: Double(max(1, runScope.count)))
            Text(titles[activeStep]).font(.system(size: 19, weight: .semibold))
            ScrollView { stepContent.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 4) }
            HStack {
                Button("Restart this setup") { step = 0; model.beginWizard(scope: runScope) }
                Spacer()
                if step > 0 { Button("Back") { step -= 1 } }
                Button(step == runScope.count - 1 ? "Finish setup" : "Save & Continue") { advance() }.keyboardShortcut(.defaultAction)
            }
        }.padding(20)
    }

    private var scopeChooser: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Setup Wizard").font(.system(size: 25, weight: .bold, design: .rounded))
            Text("Run all sections again, or select only the settings you want to edit. Unselected sections keep their saved values.")
                .foregroundStyle(.secondary)
            Button("Run the full setup") { begin(scope: Array(0..<6)) }.keyboardShortcut(.defaultAction)
            Divider()
            Text("Or select sections").font(.headline)
            ForEach(Array(titles.enumerated()), id: \.offset) { index, title in
                Toggle(title, isOn: Binding(get: { selectedScope.contains(index) }, set: { enabled in
                    if enabled { selectedScope.insert(index) } else { selectedScope.remove(index) }
                }))
            }
            HStack {
                Spacer()
                Button("Run selected sections") { begin(scope: selectedScope.sorted()) }.disabled(selectedScope.isEmpty)
            }
        }.padding(22).frame(maxWidth: 560, alignment: .leading)
    }

    private func begin(scope: [Int]) {
        guard !scope.isEmpty else { return }
        runScope = scope
        step = 0
        profile = model.activeProfile
        firstSetupRun = false
        model.beginWizard(scope: scope)
        scopeChosen = true
    }

    @ViewBuilder private var stepContent: some View {
        switch activeStep {
        case 0:
            VStack(alignment: .leading, spacing: 12) {
                Text("FloBar listens to the selected MIDI inputs and shows AIR, SMC and P23 in the status header. Battery values stay unavailable unless macOS provides a reliable reading.").foregroundStyle(.secondary)
                HStack { Text("AIRSTEP channel"); numberField($profile.controllers.airChannel) }
                HStack { Text("SMC channel"); numberField($profile.controllers.smcChannel) }
                HStack { Text("P23 channel"); numberField($profile.controllers.p23Channel) }
                Text("Detected CoreMIDI inputs: \(model.availableInputs.joined(separator: ", ").isEmpty ? "none yet" : model.availableInputs.joined(separator: ", "))").font(.caption).foregroundStyle(.secondary)
                Toggle("Enable global HID arrows and § restart", isOn: $profile.globalHideShortcutEnabled)
                Text("This opt-in listens only for Up, Down and §. FloBar does not record ordinary typing. macOS may require Input Monitoring permission.").font(.caption).foregroundStyle(.secondary)
                Button("Request Input Monitoring now") { app.requestGlobalInputMonitoring() }.disabled(!profile.globalHideShortcutEnabled)
                Text("Use Learn beside a control in the next steps, then press that control once.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        case 1:
            VStack(alignment: .leading, spacing: 8) {
                Text("Edit the effect button names and mappings. Unmapped buttons remain disabled; FloBar will never guess a CC.").foregroundStyle(.secondary)
                ForEach(profile.buttons.indices.filter { profile.buttons[$0].order >= 5 }, id: \.self) { index in buttonEditor(index) }
                Toggle("Show effect buttons", isOn: $profile.showEffects)
            }
        case 2:
            VStack(alignment: .leading, spacing: 8) {
                Text("Map the input and output controls. A MIDI Learn result is only applied after you press Learn; settings edits do not send MIDI.").foregroundStyle(.secondary)
                ForEach(profile.buttons.indices.filter { profile.buttons[$0].order < 5 }, id: \.self) { index in buttonEditor(index) }
                Toggle("Show input/output controls", isOn: $profile.showInputsOutputs)
            }
        case 3:
            VStack(alignment: .leading, spacing: 12) {
                Text("The show timer starts, pauses and resumes with a click. Reset returns to the saved standard duration.").foregroundStyle(.secondary)
                HStack { Text("Standard duration (minutes)"); numberField(Binding(get: { profile.timerDefaultSeconds / 60 }, set: { profile.timerDefaultSeconds = max(1, min(300, $0)) * 60 })) }
                Text("Maximum supported performance length: 5 hours.").font(.caption).foregroundStyle(.secondary)
            }
        case 4:
            VStack(alignment: .leading, spacing: 10) {
                Text("Messages can be stored in separate Sets, shown with their own duration, and moved with the arrows. X hides a message for this performance only. Long press or double-click anywhere in the message tile opens its settings.").foregroundStyle(.secondary)
                TextField("First reminder (up to 40 characters)", text: $firstMessage).onChange(of: firstMessage) { firstMessage = String($0.prefix(40)) }
                TextField("Second reminder (up to 40 characters)", text: $secondMessage).onChange(of: secondMessage) { secondMessage = String($0.prefix(40)) }
                Text("Long messages scroll once in the direction of the primary language, at a speed fitted to the display time. No alert sounds are used.").font(.caption).foregroundStyle(.secondary)
            }
        default:
            VStack(alignment: .leading, spacing: 12) {
                Toggle("Enable FX mute button", isOn: $profile.fxMuteEnabled)
                Text("CC43 replaces CC60. CC43 v127 enables Sidechain Bypass; v0 disables it. CC57 and CC0 are not used. FX/Sidechain colors change only after verified feedback from the external MIDI state input.").foregroundStyle(.secondary)
                HStack { Text("Sidechain CC"); Text("43 (fixed, approved)").fontWeight(.semibold) }
                HStack { Text("FX mute note"); Text("22 (existing mapping)").fontWeight(.semibold) }
                Text("Enable the optional temperature display for High and Critical system states.").foregroundStyle(.secondary)
                Toggle("Show High/Critical thermometer indicators", isOn: $profile.temperatureEnabled)
            }
        }
    }

    private func buttonEditor(_ index: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                TextField("Full name", text: $profile.buttons[index].title)
                TextField("Short name", text: $profile.buttons[index].shortTitle).frame(width: 110)
                Toggle("Shown", isOn: $profile.buttons[index].visible).labelsHidden()
                Button(learningButtonID == profile.buttons[index].id ? "Listening… press control" : "Learn") {
                    learningButtonID = profile.buttons[index].id
                }.disabled(learningButtonID != nil && learningButtonID != profile.buttons[index].id)
            }
            HStack {
                Text("Channel"); numberField($profile.buttons[index].command.channel)
                Text("CC/Note"); numberField($profile.buttons[index].command.number)
                Text("Value"); numberField($profile.buttons[index].command.value)
                Text("Full: \(profile.buttons[index].title) · Short: \(profile.buttons[index].shortTitle)").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(profile.buttons[index].inputAliases.indices, id: \.self) { aliasIndex in
                HStack {
                    Text("Alternate")
                    Picker("Type", selection: $profile.buttons[index].inputAliases[aliasIndex].kind) { ForEach(CommandKind.allCases, id: \.self) { Text($0.rawValue).tag($0) } }.frame(width: 145)
                    Text("Channel"); numberField($profile.buttons[index].inputAliases[aliasIndex].channel)
                    Text("CC/Note"); numberField($profile.buttons[index].inputAliases[aliasIndex].number)
                }
            }
            Button("Add alternate input") { profile.buttons[index].inputAliases.append(MIDICommand(number: -1)) }
        }.padding(8).background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 7))
    }

    private func numberField(_ value: Binding<Int>) -> some View {
        TextField("", value: value, format: .number).textFieldStyle(.roundedBorder).frame(width: 76)
    }

    private func learnedCommand(_ event: MIDIEvent, title: String, short: String) -> MIDICommand {
        switch event {
        case let .cc(ch, cc, value): return MIDICommand(kind: .controlChange, channel: ch, number: cc, value: value, displayName: title, shortName: short)
        case let .noteOn(ch, note, velocity): return MIDICommand(kind: .noteOn, channel: ch, number: note, value: velocity, displayName: title, shortName: short)
        case let .hid(key): return MIDICommand(kind: .hidKey, channel: 0, number: -1, key: key, displayName: title, shortName: short)
        case .noteOff: return MIDICommand(number: -1)
        }
    }

    private func applyLearned(_ event: MIDIEvent, source: String, to index: Int) {
        if case .noteOff = event { return }
        let button = profile.buttons[index]
        let learned = learnedCommand(event, title: button.title, short: button.shortTitle)
        if source.localizedCaseInsensitiveContains("smc") || source.localizedCaseInsensitiveContains("p23") || source == "Global HID" {
            profile.buttons[index].inputAliases.removeAll {
                $0.kind == learned.kind && $0.channel == learned.channel && $0.number == learned.number && $0.key == learned.key
            }
            profile.buttons[index].inputAliases.append(learned)
        } else {
            profile.buttons[index].command = learned
        }
    }

    private func advance() {
        let completedSection = activeStep
        let saved = model.mutate("Wizard checkpoint \(completedSection + 1)") { config in
            if let i = config.profiles.firstIndex(where: { $0.id == profile.id }) { config.profiles[i] = profile }
            else { config.profiles[0] = profile }
            config.wizardScope = runScope
            config.wizardCheckpoint = min(runScope.count, step + 1)
            config.wizardInProgress = true
            if step == runScope.count - 1 && firstSetupRun,
               let setIndex = config.messageSets.firstIndex(where: { $0.id == config.activeMessageSetID }) {
                while config.messageSets[setIndex].rows.count < 2 { config.messageSets[setIndex].rows.append(MessageRow()) }
                config.messageSets[setIndex].rows[0].text = String(firstMessage.prefix(40))
                config.messageSets[setIndex].rows[0].show = !firstMessage.isEmpty
                config.messageSets[setIndex].rows[1].text = String(secondMessage.prefix(40))
                config.messageSets[setIndex].rows[1].show = !secondMessage.isEmpty
            }
        }
        guard saved else { return }
        guard step == runScope.count - 1 else { step += 1; return }
        guard model.finishWizard() else { return }
        model.beginMessageDraft()
        finishedInView = true
        if firstSetupRun { offerActions = true }
    }
}

struct ControlSettingsView: View {
    @ObservedObject var model: FloBarModel
    @State private var draft = BarProfile()
    @State private var selectedProfileID = UUID()
    @State private var collisionWarning = false
    @State private var learningButtonID: UUID?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Picker("Profile", selection: $selectedProfileID) {
                    ForEach(model.configuration.profiles) { Text($0.name).tag($0.id) }
                }.onChange(of: selectedProfileID) { id in load(id) }
                Button("New Profile") {
                    var copy = model.activeProfile; copy.id = UUID()
                    let names = Set(model.configuration.profiles.map { $0.name.lowercased() })
                    var suffix = 1
                    var candidate = "New Profile"
                    while names.contains(candidate.lowercased()) { suffix += 1; candidate = "New Profile \(suffix)" }
                    copy.name = candidate
                    _ = model.mutate("Add Profile") { $0.profiles.append(copy); $0.activeProfileID = copy.id }
                    load(copy.id)
                }
                Button("Save Profile") { save() }
            }
            Form {
                TextField("Profile name", text: $draft.name)
                Picker("Bar position", selection: $draft.edge) {
                    Text("Top · horizontal").tag(BarEdge.top); Text("Bottom · horizontal").tag(BarEdge.bottom)
                    Text("Left · vertical").tag(BarEdge.left); Text("Right · vertical").tag(BarEdge.right)
                }
                Stepper("Default show timer: \(draft.timerDefaultSeconds / 60) minutes", value: Binding(get: { draft.timerDefaultSeconds / 60 }, set: { draft.timerDefaultSeconds = max(1, min(300, $0)) * 60 }), in: 1...300)
                Toggle("Remember remaining timer on Quit", isOn: Binding(
                    get: { model.configuration.rememberTimerDefault },
                    set: { value in _ = model.mutate("Remember timer preference") { $0.rememberTimerDefault = value } }
                ))
                Toggle("Show effect buttons", isOn: $draft.showEffects)
                Toggle("Show input/output controls", isOn: $draft.showInputsOutputs)
                Toggle("Show High/Critical thermal state", isOn: $draft.temperatureEnabled)
                Toggle("Enable FX mute tile", isOn: $draft.fxMuteEnabled)
            }
            Divider()
            HStack { Text("Controls").font(.headline); Spacer(); Button("Add unmapped button") { draft.buttons.append(BarButton(title: "New button", shortTitle: "New", command: MIDICommand(number: -1), order: draft.buttons.count)) } }
            List {
                ForEach(draft.buttons.indices.sorted(by: { draft.buttons[$0].order < draft.buttons[$1].order }), id: \.self) { index in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            TextField("Full name", text: $draft.buttons[index].title)
                            TextField("Short name", text: $draft.buttons[index].shortTitle).frame(width: 100)
                            Toggle("Show", isOn: $draft.buttons[index].visible).labelsHidden()
                            Button("↑") { moveButton(at: index, delta: -1) }.disabled(!canMoveButton(at: index, delta: -1))
                            Button("↓") { moveButton(at: index, delta: 1) }.disabled(!canMoveButton(at: index, delta: 1))
                        }
                        HStack {
                            Picker("Type", selection: $draft.buttons[index].command.kind) {
                                Text("CC").tag(CommandKind.controlChange); Text("Note").tag(CommandKind.noteOn)
                            }.frame(width: 160)
                            TextField("Channel", value: $draft.buttons[index].command.channel, format: .number).frame(width: 70)
                            TextField("CC/Note", value: $draft.buttons[index].command.number, format: .number).frame(width: 80)
                            TextField("Value", value: $draft.buttons[index].command.value, format: .number).frame(width: 70)
                            ColorPicker("On", selection: colorBinding($draft.buttons[index].activeColor), supportsOpacity: false).labelsHidden().help("Active feedback color")
                            TextField("#RRGGBB", text: $draft.buttons[index].activeColor).frame(width: 90)
                            ColorPicker("Off", selection: colorBinding($draft.buttons[index].inactiveColor), supportsOpacity: false).labelsHidden().help("Inactive feedback color")
                            TextField("#RRGGBB", text: $draft.buttons[index].inactiveColor).frame(width: 90)
                            Button(learningButtonID == draft.buttons[index].id ? "Listening…" : "Learn") {
                                learningButtonID = draft.buttons[index].id
                            }.disabled(learningButtonID != nil && learningButtonID != draft.buttons[index].id)
                        }
                        ForEach(draft.buttons[index].inputAliases.indices, id: \.self) { aliasIndex in
                            HStack {
                                Text("Alternate input")
                                Picker("Type", selection: $draft.buttons[index].inputAliases[aliasIndex].kind) { ForEach(CommandKind.allCases, id: \.self) { Text($0.rawValue).tag($0) } }.frame(width: 145)
                                TextField("Channel", value: $draft.buttons[index].inputAliases[aliasIndex].channel, format: .number).frame(width: 65)
                                TextField("CC/Note", value: $draft.buttons[index].inputAliases[aliasIndex].number, format: .number).frame(width: 75)
                                TextField("Key", text: $draft.buttons[index].inputAliases[aliasIndex].key).frame(width: 85)
                                Button(role: .destructive) { draft.buttons[index].inputAliases.remove(at: aliasIndex) } label: { Image(systemName: "minus.circle") }
                            }
                        }
                        Button("Add alternate input") { draft.buttons[index].inputAliases.append(MIDICommand(number: -1)) }
                    }.padding(.vertical, 3)
                }
            }.frame(minHeight: 190)
            HStack {
                Button("Undo settings") { model.undo() }.disabled(!model.canUndo)
                Button("Redo settings") { model.redo() }.disabled(!model.canRedo)
                Spacer()
                if learningButtonID != nil { Button("Cancel Learn") { learningButtonID = nil } }
                Button("Save Profile") { save() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(14)
        .onAppear { draft = model.activeProfile; selectedProfileID = draft.id }
        .onChange(of: model.midiEventGeneration) { _ in
            guard let learningButtonID,
                  let event = model.lastReceivedEvent,
                  !model.lastReceivedSource.localizedCaseInsensitiveContains("iac"),
                  !isNoteOff(event),
                  let index = draft.buttons.firstIndex(where: { $0.id == learningButtonID }) else { return }
            let learned = learnedCommand(event, title: draft.buttons[index].title, short: draft.buttons[index].shortTitle)
            if model.lastReceivedSource.localizedCaseInsensitiveContains("smc") || model.lastReceivedSource.localizedCaseInsensitiveContains("p23") || model.lastReceivedSource == "Global HID" {
                draft.buttons[index].inputAliases.append(learned)
            } else {
                draft.buttons[index].command = learned
            }
            self.learningButtonID = nil
        }
        .alert("Shared MIDI mapping", isPresented: $collisionWarning) {
            Button("Save shared mapping") { persistDraft() }
            Button("Review mappings", role: .cancel) { }
        } message: { Text("Two or more visible controls use the same MIDI command. Saving creates a shared mapping that can trigger more than one action.") }
    }

    private func load(_ id: UUID) {
        guard let found = model.configuration.profiles.first(where: { $0.id == id }) else { return }
        draft = found; _ = model.mutate("Select Profile") { $0.activeProfileID = id }
    }

    private func save() {
        var keys: [String: Int] = [:]
        for item in draft.buttons where item.visible && item.command.number >= 0 {
            let key = "\(item.command.kind):\(item.command.channel):\(item.command.number)"
            keys[key, default: 0] += 1
        }
        if keys.values.contains(where: { $0 > 1 }) { collisionWarning = true } else { persistDraft() }
    }

    private func persistDraft() {
        _ = model.mutate("Edit Profile") { config in
            if let index = config.profiles.firstIndex(where: { $0.id == draft.id }) { config.profiles[index] = draft }
            else { config.profiles.append(draft) }
            config.activeProfileID = draft.id
        }
    }

    private func canMoveButton(at index: Int, delta: Int) -> Bool {
        let sorted = draft.buttons.indices.sorted { draft.buttons[$0].order < draft.buttons[$1].order }
        guard let position = sorted.firstIndex(of: index) else { return false }
        return sorted.indices.contains(position + delta)
    }

    private func moveButton(at index: Int, delta: Int) {
        let sorted = draft.buttons.indices.sorted { draft.buttons[$0].order < draft.buttons[$1].order }
        guard let position = sorted.firstIndex(of: index), sorted.indices.contains(position + delta) else { return }
        let neighbor = sorted[position + delta]
        let currentOrder = draft.buttons[index].order
        draft.buttons[index].order = draft.buttons[neighbor].order
        draft.buttons[neighbor].order = currentOrder
    }

    private func colorBinding(_ hex: Binding<String>) -> Binding<Color> {
        Binding(get: { Color(hex: hex.wrappedValue) }, set: { color in
            guard let rgb = NSColor(color).usingColorSpace(.deviceRGB) else { return }
            hex.wrappedValue = String(format: "#%02X%02X%02X", Int(rgb.redComponent * 255), Int(rgb.greenComponent * 255), Int(rgb.blueComponent * 255))
        })
    }

    private func isNoteOff(_ event: MIDIEvent) -> Bool {
        if case .noteOff = event { return true }
        return false
    }

    private func learnedCommand(_ event: MIDIEvent, title: String, short: String) -> MIDICommand {
        switch event {
        case let .cc(ch, cc, value): return MIDICommand(kind: .controlChange, channel: ch, number: cc, value: value, displayName: title, shortName: short)
        case let .noteOn(ch, note, velocity): return MIDICommand(kind: .noteOn, channel: ch, number: note, value: velocity, displayName: title, shortName: short)
        case let .hid(key): return MIDICommand(kind: .hidKey, channel: 0, number: -1, key: key, displayName: title, shortName: short)
        case .noteOff: return MIDICommand(number: -1)
        }
    }
}

struct MessageSetSettingsView: View {
    @ObservedObject var model: FloBarModel
    @State private var selectedID = UUID()
    @State private var presetName = ""
    @State private var overwriteName = ""
    @State private var showOverwrite = false
    @State private var showRestore = false
    @State private var showDraftExit = false
    @State private var queuedLoadID: UUID?
    @State private var status = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Picker("Set", selection: $selectedID) {
                    ForEach(model.configuration.messageSets) { Text($0.name).tag($0.id) }
                }
                Button("Select") { requestLoad(selectedID) }
                Button("Cancel") { model.discardMessageDraft(); selectedID = model.configuration.activeMessageSetID; presetName = model.activeMessageSet.name; status = "Changes discarded." }
                Button("↑") { moveSet(-1) }.disabled(!canMoveSet(-1)).help("Move Set up")
                Button("↓") { moveSet(1) }.disabled(!canMoveSet(1)).help("Move Set down")
                Spacer()
                TextField("Preset name", text: $presetName)
                    .frame(width: 190).onSubmit { loadByName() }
                Button("Add new name") { save() }
            }
            if let draft = model.messageDraft {
                HStack {
                    Text("Active Set:").foregroundStyle(.secondary)
                    Text(draft.name).fontWeight(.semibold)
                    Spacer()
                    TextField("Ready text", text: Binding(get: { model.messageDraft?.readyText ?? "READY" }, set: { value in model.updateMessageDraft { $0.readyText = String(value.prefix(40)) } })).frame(width: 130)
                    TextField("Ready color", text: Binding(get: { model.messageDraft?.readyColor ?? "#263548" }, set: { value in model.updateMessageDraft { $0.readyColor = value } })).frame(width: 110)
                }
                HStack { Text("#").frame(width: 26); Text("Message text (max 40 characters)"); Spacer(); Text("Seconds"); Text("Global"); Text("Show") }
                    .font(.caption).foregroundStyle(.secondary)
                ScrollView {
                    VStack(spacing: 5) {
                        ForEach(Array(draft.rows.enumerated()), id: \.element.id) { index, row in
                            HStack(spacing: 7) {
                                Text("\(index + 1)").frame(width: 26, alignment: .leading)
                                TextField("Message", text: Binding(get: { model.messageDraft?.rows[index].text ?? "" }, set: { text in model.updateMessageDraft { $0.rows[index].text = String(text.prefix(40)) } }))
                                    .textFieldStyle(.roundedBorder)
                                TextField("", value: Binding(get: { model.messageDraft?.rows[index].seconds ?? 10 }, set: { value in model.updateMessageDraft { $0.rows[index].seconds = max(1, value) } }), format: .number).frame(width: 70)
                                Toggle("", isOn: Binding(get: { model.messageDraft?.rows[index].global ?? true }, set: { value in model.updateMessageDraft { $0.rows[index].global = value } })).labelsHidden()
                                Toggle("", isOn: Binding(get: { model.messageDraft?.rows[index].show ?? true }, set: { value in model.updateMessageDraft { $0.rows[index].show = value } })).labelsHidden()
                            }
                        }
                    }
                }
            }
            HStack {
                Text("Messages are limited to 40 grapheme clusters. Long text scrolls once in the primary language direction; no sound alerts.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Restore Previous Set") { showRestore = true }
                Button("Save changes") { save() }.keyboardShortcut(.defaultAction)
            }
            if !status.isEmpty { Text(status).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(14)
        .onAppear { load(model.configuration.activeMessageSetID) }
        .alert("Replace existing Set?", isPresented: $showOverwrite) {
            Button("Overwrite \(overwriteName)", role: .destructive) {
                let error = model.saveMessageDraft(as: overwriteName, confirmedOverwrite: true)
                status = error ?? "Set saved."
                selectedID = model.configuration.activeMessageSetID
                model.beginMessageDraft()
                if error == nil, let queuedLoadID { loadNow(queuedLoadID) }
            }
            Button("Choose a new name") { presetName = "" }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Saving will replace the settings in \(overwriteName). FloBar keeps one previous-Set snapshot; the next overwrite replaces that snapshot, after which these previous settings cannot be recovered.")
        }
        .alert("Restore previous Set?", isPresented: $showRestore) {
            Button("Restore", role: .destructive) {
                if let name = model.restorePreviousSet() { status = "Restored \(name)."; load(model.configuration.activeMessageSetID) }
                else { status = "No previous Set snapshot is available." }
            }
            Button("Cancel", role: .cancel) { }
        } message: { Text("The saved snapshot will replace the current Set with the same name.") }
        .alert("Unsaved message changes", isPresented: $showDraftExit) {
            Button("Save") { saveBeforeLoad() }
            Button("Discard", role: .destructive) {
                model.discardMessageDraft()
                if let queuedLoadID { loadNow(queuedLoadID) }
            }
            Button("Cancel", role: .cancel) { queuedLoadID = nil; selectedID = model.configuration.activeMessageSetID }
        } message: { Text("Save, discard, or cancel before leaving this Set.") }
    }

    private func load(_ id: UUID) {
        requestLoad(id)
    }

    private func requestLoad(_ id: UUID) {
        guard id != model.configuration.activeMessageSetID || model.messageDraftDirty else { return }
        if model.messageDraftDirty { queuedLoadID = id; showDraftExit = true }
        else { loadNow(id) }
    }

    private func loadNow(_ id: UUID) {
        model.selectMessageSet(id)
        model.beginMessageDraft()
        selectedID = id
        presetName = model.messageDraft?.name ?? ""
        status = ""
    }

    private func loadByName() {
        guard let found = model.configuration.messageSets.first(where: { $0.name.caseInsensitiveCompare(presetName) == .orderedSame }) else {
            status = "No saved Set has that name."; return
        }
        requestLoad(found.id)
    }

    private func saveBeforeLoad() {
        let result = model.saveMessageDraft(as: presetName)
        if let result, result.hasPrefix("EXISTS:") {
            overwriteName = String(result.dropFirst("EXISTS:".count)); showOverwrite = true
        } else if let result {
            status = result; queuedLoadID = nil
        } else if let queuedLoadID {
            loadNow(queuedLoadID)
        }
    }

    private func save() {
        let result = model.saveMessageDraft(as: presetName)
        if let result, result.hasPrefix("EXISTS:") { overwriteName = String(result.dropFirst("EXISTS:".count)); showOverwrite = true }
        else if let result { status = result }
        else {
            status = "Set saved."; selectedID = model.configuration.activeMessageSetID; model.beginMessageDraft()
            if let queuedLoadID { loadNow(queuedLoadID) }
        }
    }

    private func canMoveSet(_ delta: Int) -> Bool {
        guard let index = model.configuration.messageSets.firstIndex(where: { $0.id == selectedID }) else { return false }
        return model.configuration.messageSets.indices.contains(index + delta)
    }

    private func moveSet(_ delta: Int) {
        guard let index = model.configuration.messageSets.firstIndex(where: { $0.id == selectedID }),
              model.configuration.messageSets.indices.contains(index + delta) else { return }
        _ = model.mutate("Reorder Message Sets") { config in
            config.messageSets.swapAt(index, index + delta)
        }
    }
}

struct SecondaryActionSettingsView: View {
    @ObservedObject var model: FloBarModel
    @State private var profile = BarProfile()
    @State private var status = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("TT1 Secondary Action Builder").font(.system(size: 22, weight: .bold, design: .rounded))
            Text("These controls configure the runtime TT1 state machine. The approved default timings are prefilled. Saving settings never sends MIDI; mapped commands run only when the trigger or branch arrives during a performance.")
                .font(.callout).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    GroupBox("Trigger and branches") {
                        VStack(alignment: .leading, spacing: 8) {
                            Toggle("Enable TT1", isOn: $profile.tt1.enabled)
                            HStack {
                                Picker("Trigger type", selection: $profile.tt1.trigger.kind) {
                                    Text("CC").tag(CommandKind.controlChange)
                                    Text("Note").tag(CommandKind.noteOn)
                                }.frame(width: 125)
                                TextField("Channel", value: $profile.tt1.trigger.channel, format: .number).frame(width: 75)
                                TextField("CC / Note", value: $profile.tt1.trigger.number, format: .number).frame(width: 90)
                                TextField("Value", value: $profile.tt1.trigger.value, format: .number).frame(width: 75)
                            }
                            Toggle("G2M branch during the initial window", isOn: $profile.tt1.enableG2MBranch)
                            Toggle("TalkBack branch during the initial window", isOn: $profile.tt1.enableTalkBackBranch)
                            Toggle("CC22 quick-fade branch during TT1", isOn: $profile.tt1.enableQuickFadeBranch)
                            Toggle("Down → P → L → Up branch during TT1", isOn: $profile.tt1.enableDownBranch)
                        }
                    }
                    GroupBox("Fade and focus timing") {
                        VStack(alignment: .leading, spacing: 7) {
                            timing("G2M / TalkBack branch window", $profile.tt1.specialWindowSeconds, range: 0.1...30, step: 0.1)
                            timing("Full fade duration", $profile.tt1.fullFadeSeconds, range: 1...120, step: 0.5)
                            timing("Quick fade duration", $profile.tt1.quickFadeSeconds, range: 0.1...10, step: 0.1)
                            timing("CC22 quick-fade P target", $profile.tt1.quickFadePlayDelaySeconds, range: 0...10, step: 0.05)
                            timing("Down P target", $profile.tt1.downPlayDelaySeconds, range: 0...10, step: 0.05)
                            timing("JamZone foreground settle in split", $profile.tt1.splitPlaySettleSeconds, range: 0...5, step: 0.05)
                            timing("JamZone foreground settle otherwise", $profile.tt1.normalPlaySettleSeconds, range: 0...5, step: 0.05)
                        }
                    }
                    GroupBox("Down sequence and recovery") {
                        VStack(alignment: .leading, spacing: 7) {
                            timing("P → L delay", $profile.tt1.playToLoopSeconds, range: 0...5, step: 0.05)
                            timing("L → Up delay", $profile.tt1.loopToUpSeconds, range: 0...5, step: 0.05)
                            timing("Up → quick-volume restore", $profile.tt1.upToRestoreSeconds, range: 0...5, step: 0.05)
                            timing("CC43 v127 → v0", $profile.tt1.sidechainRestoreSeconds, range: 0.1...60, step: 0.5)
                            timing("Full-volume restore after quick fade", $profile.tt1.fullVolumeRestoreSeconds, range: 0...120, step: 1)
                            timing("Matching-command window", $profile.tt1.repeatWindowSeconds, range: 0.1...60, step: 0.1)
                            Toggle("A matching repeated command renews the window", isOn: $profile.tt1.renewRepeatWindowOnMatch)
                            timing("Lockout after TT1 cancellation", $profile.tt1.cancelLockoutSeconds, range: 0...60, step: 0.5)
                        }
                    }
                    Text("The repeat window means the 2-second return window in TT1. CC43 sends v127 at quick-fade start and v0 after the configured delay. A cancelled sequence is abandoned; restart does not replay MIDI.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                if !status.isEmpty { Text(status).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("Reload saved settings") { profile = model.activeProfile; status = "Discarded unsaved builder edits." }
                Button("Save TT1 settings") {
                    profile.tt1.trigger.channel = min(16, max(1, profile.tt1.trigger.channel))
                    profile.tt1.trigger.number = min(127, max(0, profile.tt1.trigger.number))
                    profile.tt1.trigger.value = min(127, max(0, profile.tt1.trigger.value))
                    let saved = model.mutate("Save TT1 Builder") { config in
                        guard let index = config.profiles.firstIndex(where: { $0.id == profile.id }) else { return }
                        config.profiles[index].tt1 = profile.tt1
                    }
                    status = saved ? "TT1 settings saved." : "Could not save TT1 settings."
                }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(14)
        .onAppear { profile = model.activeProfile }
    }

    private func timing(_ title: String, _ value: Binding<Double>, range: ClosedRange<Double>, step: Double) -> some View {
        Stepper("\(title): \(value.wrappedValue, specifier: "%.2f")s", value: value, in: range, step: step)
    }
}

struct PrivacyBackupSettingsView: View {
    @ObservedObject var model: FloBarModel
    let app: FloBarApplication
    @State private var loginItem = false
    @State private var message = ""

    var body: some View {
        Form {
            Section("System access") {
                Toggle("Global HID arrows and § restart", isOn: Binding(get: { model.activeProfile.globalHideShortcutEnabled }, set: { value in
                    _ = model.mutate("Global input monitoring preference") { $0.profiles[0].globalHideShortcutEnabled = value }
                    if value { app.requestGlobalInputMonitoring() }
                }))
                Text("Only Up, Down and § are handled. FloBar does not retain ordinary key presses. macOS permission is requested only after you enable this option.").font(.caption).foregroundStyle(.secondary)
                Button("Request Input Monitoring") { app.requestGlobalInputMonitoring() }
                Toggle("Start FloBar after login (optional)", isOn: $loginItem).onChange(of: loginItem) { enabled in
                    do { try app.registerLoginItem(enabled); message = enabled ? "FloBar will start after login." : "Login launch disabled." }
                    catch { message = error.localizedDescription; loginItem = false }
                }
                Text("When enabled, FloBar is the single Login Item and starts one guardian. This option requires macOS 13 or later; it is off by default.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Approved storage locations") {
                Text("Settings & snapshots: \(model.appSupportURL.path)").textSelection(.enabled)
                Text("Diagnostics: \(model.logsURL.path)").textSelection(.enabled)
                Text("Legacy import: only a specific JSON file chosen by you in Open; no directory scan or Full Disk Access.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Backup and migration") {
                HStack { Button("Import legacy message settings…") { app.importLegacySettings() }; Button("Import FloBar backup…") { app.importBackup() }; Button("Export backup…") { app.exportBackup() } }
                Text("The legacy source is read only and is never opened automatically. Backup imports show a confirmation before replacing settings.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Runtime protections") {
                Text("Guardian: heartbeat 1s; hang at 5s; maximum 3 automatic relaunches in 60s. Hide keeps FloBar and guardian running; Quit stops both. Interrupted MIDI/TT1 sequences are abandoned and are never replayed.")
                Text("CPU target applies to FloBar plus guardian: ≤7% normal and ≤10% peak. Stability and live MIDI response take priority; the measured result must be reported.")
            }
            if !message.isEmpty { Text(message).foregroundStyle(.secondary) }
        }
        .padding(14)
        .onAppear { loginItem = model.configuration.startOnLogin }
    }
}
