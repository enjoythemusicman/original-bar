import AppKit
import FloBarCore
import SwiftUI
import UniformTypeIdentifiers

struct FloBarView: View {
    @ObservedObject var model: FloBarModel
    let midi: CoreMIDIService
    let app: FloBarApplication

    private let tileHeight: CGFloat = 56
    private let gripHeight: CGFloat = 7
    @State private var clockNow = Date()
    @State private var clockTimer: Timer?

    private static let clockFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.dateStyle = .none
        return formatter
    }()

    var body: some View {
        let vertical = model.activeProfile.edge.isVertical
        Group {
            if vertical {
                ScrollView(.vertical) {
                    VStack(spacing: 3) {
                        panelControls
                        ForEach(model.activeProfile.layoutOrder, id: \.self) { item in
                            layoutItem(item, vertical: true)
                        }
                    }.padding(4)
                }
            } else {
                HStack(spacing: 3) {
                    panelControls
                    ForEach(model.activeProfile.layoutOrder, id: \.self) { item in
                        layoutItem(item, vertical: false)
                    }
                }
                .padding(4)
            }
        }
        .background(Color(hex: "101923").opacity(0.98))
        .clipShape(RoundedRectangle(cornerRadius: vertical ? 12 : 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: vertical ? 12 : 16).stroke(Color.white.opacity(0.17), lineWidth: 1))
        .shadow(color: .black.opacity(0.28), radius: 9, y: 3)
        .onChange(of: model.activeProfile.edge) { app.repositionBar($0) }
        .onAppear { startClockUpdates() }
        .onDisappear { clockTimer?.invalidate(); clockTimer = nil }
        .alert("FloBar", isPresented: Binding(get: { model.lastError != nil }, set: { if !$0 { model.lastError = nil } })) {
            Button("OK", role: .cancel) { model.lastError = nil }
        } message: { Text(model.lastError ?? "") }
    }

    private var panelControls: some View {
        VStack(spacing: 1) {
            Button { app.quitFromBar() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(Color(hex: "E46C6C"))
                    .frame(width: 18, height: 18)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Quit FloBar")
            DragHandle(onDragStart: { app.beginPanelDrag() }, onDrag: { delta in app.movePanel(by: delta) }, onDragEnd: { app.endPanelDrag() })
                .frame(width: 17, height: 28)
                .overlay(Image(systemName: "line.3.horizontal")
                    .rotationEffect(model.activeProfile.edge.isVertical ? .degrees(90) : .zero)
                    .foregroundStyle(.white.opacity(0.42))
                    .font(.system(size: 11, weight: .medium)))
                .help("Drag FloBar to reposition")
        }
        .frame(width: 18)
    }

    @ViewBuilder
    private func layoutItem(_ item: String, vertical: Bool) -> some View {
        switch item {
        case "message":
            movableTopLevel("message", vertical: vertical) {
                messageTile.frame(height: vertical ? 116 : tileHeight - gripHeight)
            }.frame(width: vertical ? nil : 320)
        case "fx":
            if model.activeProfile.fxMuteEnabled {
                movableTopLevel("fx", vertical: vertical) { fxTile(vertical: vertical) }
                    .frame(width: vertical ? nil : 57)
            }
        case "ioSection":
            if model.activeProfile.showInputsOutputs {
                movableTopLevel("ioSection", vertical: vertical) { sectionTile("INPUTS & OUTPUTS", section: "ioSection", vertical: vertical) }
                    .frame(maxWidth: vertical ? .infinity : nil)
            }
        case "effectsSection":
            if model.activeProfile.showEffects {
                movableTopLevel("effectsSection", vertical: vertical) { sectionTile("EFFECTS", section: "effectsSection", vertical: vertical) }
                    .frame(maxWidth: vertical ? .infinity : nil)
            }
        case "midiInput":
            movableTopLevel("midiInput", vertical: vertical) {
                midiInputTile.frame(width: vertical ? nil : 101, height: tileHeight - gripHeight)
            }.frame(maxWidth: vertical ? .infinity : nil)
        case "ready":
            movableTopLevel("ready", vertical: vertical) { readyTile.frame(height: tileHeight - gripHeight) }
                .frame(width: vertical ? nil : 57)
        case "timerStack":
            movableTopLevel("timerStack", vertical: vertical) { timerStack.frame(height: tileHeight - gripHeight) }
                .frame(width: vertical ? nil : 57)
        case "bpm":
            movableTopLevel("bpm", vertical: vertical) { bpmTile.frame(height: tileHeight - gripHeight) }
                .frame(width: vertical ? nil : 57)
        default:
            EmptyView()
        }
    }

    private func movableTopLevel<Content: View>(_ itemID: String, vertical: Bool, @ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: 0) {
            ReorderPressHandle(vertical: vertical, stepLength: 48, onMove: { delta in
                _ = model.moveLayoutItem(itemID, by: delta)
            })
            .overlay(DotGrip())
            .frame(height: gripHeight)
            content()
        }
        .frame(maxWidth: vertical ? .infinity : nil)
        .contentShape(Rectangle())
    }

    private var messageTile: some View {
        MessageTile(model: model, app: app)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(hex: "1B2935"))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.white.opacity(0.12), lineWidth: 1))
    }

    private func buttonTile(_ button: BarButton, section: String, vertical: Bool) -> some View {
        let feedback = model.stateColors[button.id.uuidString]
        let fill = feedback == "on" ? Color(hex: button.activeColor) : feedback == "off" ? Color(hex: button.inactiveColor) : Color(hex: "263443")
        return VStack(spacing: 0) {
            ReorderPressHandle(vertical: vertical, stepLength: vertical ? 40 : 48, onMove: { delta in
                _ = model.moveButton(button.id, within: section, by: delta)
            })
            .overlay(DotGrip())
            .frame(height: 7)
            Button {
                if ["play", "loop", "goBack", "hide", "split"].contains(button.role) {
                    app.activateBarButton(button)
                } else {
                    sendButtonCommand(button, confirmedState: feedback)
                }
            } label: {
                Text(button.shortTitle).font(.system(size: 17, weight: .bold, design: .rounded))
                    .lineLimit(1).minimumScaleFactor(0.64).frame(maxWidth: .infinity, maxHeight: .infinity)
                    .foregroundStyle(Color.white.opacity(feedback == nil ? 0.82 : 0.98))
                    .background(fill)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.white.opacity(0.13), lineWidth: 1))
            }
            .buttonStyle(.plain)
            .disabled(button.command.number < 0 || button.command.kind == .hidKey)
            .help("\(button.title) — \(feedback ?? "Unknown; waiting for verified MIDI feedback")")
        }
        .frame(height: model.activeProfile.compactSections ? 48 : 56)
    }

    private func sendButtonCommand(_ button: BarButton, confirmedState: String?) {
        guard button.command.number >= 0, button.command.kind != .hidKey else { return }
        let togglesState = ["fxMute", "g2m", "monitorMute", "bvox", "solos", "sidechain", "guitarMute"].contains(button.role)
        let value = togglesState ? (confirmedState == "on" ? 0 : 127) : button.command.value
        switch button.command.kind {
        case .controlChange:
            // The approved IAC feedback/control path uses channel 15, while controller
            // input channels are configured separately on the profile mapping.
            midi.sendCC(channel: 15, number: button.command.number, value: value, origin: .localButton)
        case .noteOn:
            midi.sendNote(channel: 15, number: button.command.number, velocity: value, origin: .localButton)
        case .hidKey:
            break
        }
    }

    private var midiInputTile: some View {
        VStack(spacing: 2) {
            Text("MIDI INPUT").font(.system(size: 9.9, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.55))
            Text(model.displayIACText ? (model.pairedIACText ?? model.currentMIDIText) : model.currentMIDIText)
                .font(.system(size: 14.3, weight: .bold, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.62)
                .foregroundStyle(.white.opacity(0.92))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "1B2935"))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .help("Most recent controller event and its paired IAC output")
    }

    private var readyTile: some View {
        VStack(spacing: 2) {
            Text(model.readyStatusText)
                .font(.system(size: 17.6, weight: .heavy, design: .rounded))
                .lineLimit(2).minimumScaleFactor(0.58).multilineTextAlignment(.center)
                .foregroundStyle(Color(hex: model.readyStatusColor))
            Text(model.activeMessageSet.name)
                .font(.system(size: 9.9, weight: .medium)).lineLimit(1).minimumScaleFactor(0.6)
                .foregroundStyle(.white.opacity(0.72))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "263548"))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .help("Current TT1 / Ready status · active Message Set: \(model.activeMessageSet.name)")
    }

    private var timerStack: some View {
        GeometryReader { geometry in
            VStack(spacing: 1) {
                Button { model.startOrPauseTimer() } label: {
                    VStack(spacing: 0) {
                        Text(Self.clockFormatter.string(from: clockNow))
                            .font(.system(size: 14.3, weight: .heavy, design: .rounded))
                            .foregroundStyle(Color(hex: "FFFF00"))
                            .lineLimit(1).minimumScaleFactor(0.65)
                        HStack(spacing: 2) {
                            Image(systemName: model.timerRunning ? "pause.fill" : "play.fill")
                                .font(.system(size: 10, weight: .bold))
                            Text(timerString).font(.system(size: 12.1, weight: .bold, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.65)
                        }
                        .foregroundStyle(model.timerOvertime ? Color.orange : Color.white)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(hex: "303C49"))
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                }
                .buttonStyle(.plain)
        .frame(height: (geometry.size.height - 1) * 0.5)
                .help("Start or pause the show timer")
                Button { model.resetTimer() } label: {
                    Image(systemName: "arrow.counterclockwise")
                        .font(.system(size: 13, weight: .bold))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(Color(hex: "303C49"))
                        .clipShape(RoundedRectangle(cornerRadius: 5))
                        .foregroundStyle(.white.opacity(0.85))
                }
                .buttonStyle(.plain)
                .frame(height: (geometry.size.height - 1) * 0.5)
                .help("Reset show timer")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var timerString: String {
        let seconds = max(0, Int(model.timerRemaining))
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private func startClockUpdates() {
        clockTimer?.invalidate()
        clockNow = Date()
        let timer = Timer(timeInterval: 30, repeats: true) { _ in clockNow = Date() }
        clockTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private var bpmTile: some View {
        VStack(spacing: 2) {
            Text("BPM").font(.system(size: 13, weight: .heavy, design: .rounded)).foregroundStyle(Color(hex: "E6C778"))
            Text("—").font(.system(size: 11, weight: .medium, design: .monospaced)).foregroundStyle(.white.opacity(0.7))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "263443"))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .help("External BPM source is reserved for a future extension")
    }

    private func fxTile(vertical: Bool) -> some View {
        let mute = model.activeProfile.buttons.first(where: { $0.role == "fxMute" })
        let sidechain = model.activeProfile.buttons.first(where: { $0.role == "sidechain" })
        let muteState = mute.flatMap { model.stateColors[$0.id.uuidString] }
        let sidechainState = sidechain.flatMap { model.stateColors[$0.id.uuidString] }
        let fill: Color = muteState == "on" ? Color.red : sidechainState == "on" ? Color.green : Color(hex: "355A80")
        return Text("FX")
            .font(.system(size: 17, weight: .bold, design: .rounded))
            .foregroundStyle(.white.opacity(0.92))
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(fill)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay(FXPressHandle { wasLongPress in
                guard let button = wasLongPress ? sidechain : mute else { return }
                let state = model.stateColors[button.id.uuidString]
                sendButtonCommand(button, confirmedState: state)
            })
            .help("Click: FX Mute only · Hold: FX Sidechain only. Colors follow verified IAC feedback.")
    }

    private func sectionTile(_ title: String, section: String, vertical: Bool) -> some View {
        let roles = section == "ioSection"
            ? Set(["g2m", "talkBack", "monitorMute", "bvox", "solos"])
            : Set(["delay", "chorus", "distortion", "overdrive", "flanger", "wah", "infrared"])
        let buttons = model.activeProfile.buttons.filter { button in
            button.visible && roles.contains(button.role)
        }.sorted { $0.order < $1.order }
        return VStack(spacing: 1) {
            Text(title).font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(.white.opacity(0.6)).frame(maxWidth: .infinity)
            if vertical {
                VStack(spacing: 2) {
                    ForEach(buttons) { button in buttonTile(button, section: section, vertical: true).frame(height: model.activeProfile.compactSections ? 48 : 56) }
                }
            } else {
                HStack(spacing: 2) {
                    ForEach(buttons) { button in buttonTile(button, section: section, vertical: false).frame(width: 57, height: model.activeProfile.compactSections ? 48 : 56) }
                }
            }
        }
        .padding(.horizontal, 2).padding(.bottom, 1)
        .background(Color(hex: "182531"))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.white.opacity(0.11), lineWidth: 1))
    }
}

private struct MessageTile: View {
    @ObservedObject var model: FloBarModel
    let app: FloBarApplication
    @State private var hovering = false
    @State private var pointerX: CGFloat = 0
    @State private var flashing = false
    @State private var messageRotationTimer: Timer?

    var body: some View {
        GeometryReader { proxy in
            VStack(spacing: 0) {
                HStack(spacing: 2) {
                    controllerCell(.air, name: model.activeProfile.controllers.airName, battery: model.batteryLevels[.air], reserveLeading: true)
                    controllerCell(.smc, name: model.activeProfile.controllers.smcName, battery: model.batteryLevels[.smc])
                    controllerCell(.p23, name: model.activeProfile.controllers.p23Name, battery: model.batteryLevels[.p23])
                }
                .frame(height: proxy.size.height * 0.5)
                .overlay(alignment: .topLeading) {
                    Button { app.openSettings() } label: {
                        Image(systemName: "gearshape.fill").font(.system(size: 10, weight: .semibold)).foregroundStyle(.white.opacity(0.82))
                            .frame(width: 17, height: 17).background(Color.black.opacity(0.28), in: Circle())
                    }.buttonStyle(.plain).help("Settings")
                }
                ZStack {
                    if let message = currentText {
                        MessageMarquee(text: message.text, duration: currentDisplayDuration, availableWidth: max(50, proxy.size.width - 18))
                            .font(.system(size: 17, weight: .bold, design: .rounded))
                            .foregroundStyle(Color.white.opacity(0.94))
                            .padding(.horizontal, 12)
                    } else {
                        Text("MIDI INPUT").font(.system(size: 14, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.72))
                    }
                    if hovering {
                        if pointerX >= proxy.size.width / 2 {
                            arrow("chevron.right", side: .right).position(x: proxy.size.width * 0.81, y: proxy.size.height * 0.25)
                            closeControl.position(x: proxy.size.width * 0.96, y: proxy.size.height * 0.25)
                        } else {
                            closeControl.position(x: proxy.size.width * 0.04, y: proxy.size.height * 0.25)
                            arrow("chevron.left", side: .left).position(x: proxy.size.width * 0.19, y: proxy.size.height * 0.25)
                        }
                    }
                    HoverPositionReader { point in pointerX = point.x }
                        .allowsHitTesting(false)
                }
                .frame(maxWidth: .infinity).frame(height: proxy.size.height * 0.5)
                .overlay(alignment: .leading) {
                    if model.activeProfile.temperatureEnabled, model.thermalLevel > 0 {
                        temperatureSlot(width: max(proxy.size.width * (model.thermalLevel == 2 ? 0.05 : 0.03), 28),
                                        height: proxy.size.height * 0.5)
                    }
                }
                .overlay(alignment: .trailing) {
                    if model.activeProfile.temperatureEnabled, model.thermalLevel == 2 {
                        temperatureSlot(width: max(proxy.size.width * 0.05, 28), height: proxy.size.height * 0.5)
                    }
                }
            }
            .onHover { hovering = $0 }
            .contentShape(Rectangle())
            .simultaneousGesture(TapGesture(count: 2).onEnded { app.openSettings() })
            .simultaneousGesture(LongPressGesture(minimumDuration: 0.55).onEnded { _ in app.openSettings() })
            .onDisappear {
                thermalFlashTimer?.invalidate(); thermalFlashTimer = nil
                messageRotationTimer?.invalidate(); messageRotationTimer = nil
            }
            .onChange(of: model.thermalLevel) { level in updateFlashing(for: level) }
            .onChange(of: model.currentMessageIndex) { _ in scheduleMessageRotation() }
            .onChange(of: model.visibleMessages) { _ in scheduleMessageRotation() }
            .onAppear { updateFlashing(for: model.thermalLevel); scheduleMessageRotation() }
        }
        .accessibilityElement(children: .contain)
    }

    private var currentText: MessageRow? {
        let rows = model.visibleMessages
        guard !rows.isEmpty else { return nil }
        return rows[min(model.currentMessageIndex, rows.count - 1)]
    }

    private var currentDisplayDuration: Int {
        guard let currentText else { return 10 }
        return max(1, currentText.global ? model.activeMessageSet.globalDuration : currentText.seconds)
    }

    private func scheduleMessageRotation() {
        messageRotationTimer?.invalidate(); messageRotationTimer = nil
        guard model.visibleMessages.count > 1 else { return }
        let timer = Timer(timeInterval: TimeInterval(currentDisplayDuration), repeats: false) { _ in
            DispatchQueue.main.async { model.advanceMessage(1) }
        }
        messageRotationTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    @State private var thermalFlashTimer: Timer?
    private func updateFlashing(for level: Int) {
        thermalFlashTimer?.invalidate(); thermalFlashTimer = nil
        flashing = level == 2
        guard level == 2 else { return }
        let timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
            DispatchQueue.main.async { flashing.toggle() }
        }
        thermalFlashTimer = timer
    }

    private func controllerCell(_ kind: ControllerKind, name: String, battery: Int?, reserveLeading: Bool = false) -> some View {
        let disconnected = model.controllerConnectionKnown.contains(kind) && !model.controllerOnline.contains(kind)
        let text = model.lastCommandController == kind ? (model.lastCommandName ?? name) : name
        return HStack(spacing: 3) {
            Text(text).lineLimit(1).minimumScaleFactor(0.65)
                .padding(.leading, reserveLeading ? 15 : 0)
            Spacer(minLength: 1)
            Text(battery.map { "\($0)%" } ?? "--%")
                .foregroundStyle(battery == nil ? Color.white.opacity(0.38) : Color.white.opacity(0.78))
        }
        .font(.system(size: 10, weight: .bold, design: .rounded))
        .padding(.horizontal, 4).frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(disconnected ? Color(hex: "FF0000") : Color(hex: "304250"))
        .clipShape(RoundedRectangle(cornerRadius: 3))
    }

    private var temperatureIcon: some View {
        Text("🌡️").font(.system(size: 18, weight: .heavy))
            .padding(.horizontal, model.thermalLevel == 2 ? 4 : 2).padding(.vertical, 1)
    }

    private func temperatureSlot(width: CGFloat, height: CGFloat) -> some View {
        temperatureIcon
            .frame(width: width, height: height)
            .background(model.thermalLevel == 2 ? (flashing ? Color.red : Color.yellow) : Color.orange)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .allowsHitTesting(false)
    }

    private enum Side { case left, right }
    private func arrow(_ icon: String, side: Side) -> some View {
        Button { model.advanceMessage(side == .left ? -1 : 1) } label: {
            Image(systemName: icon).font(.system(size: 37, weight: .black)).foregroundStyle(Color.white.opacity(0.57))
                .frame(width: 52, height: 48).contentShape(Rectangle())
        }.buttonStyle(.plain).help(side == .left ? "Previous message" : "Next message")
    }

    private var closeControl: some View {
        Button { model.temporaryHideCurrentMessage() } label: {
            Text("×").font(.system(size: 29, weight: .bold, design: .rounded)).foregroundStyle(Color.white.opacity(0.72))
                .frame(width: 27, height: 48).contentShape(Rectangle())
        }.buttonStyle(.plain).help("Temporarily hide this message")
    }
}

private struct MessageMarquee: View {
    let text: String
    let duration: Int
    let availableWidth: CGFloat
    @State private var start = Date()
    @State private var progress = 0.0
    @State private var scrollTimer: Timer?

    private var textWidth: CGFloat {
        (text as NSString).size(withAttributes: [.font: NSFont.systemFont(ofSize: 17, weight: .bold)]).width
    }
    private var distance: CGFloat { max(0, textWidth - availableWidth) }
    private var rtl: Bool { primaryLanguageDirection(text) }

    var body: some View {
        let scrolls = distance > 2
        return Text(text)
            .lineLimit(1).fixedSize(horizontal: true, vertical: false)
            .frame(width: max(textWidth, availableWidth), alignment: rtl ? .trailing : .leading)
            .offset(x: scrolls ? (rtl ? distance * progress : -distance * progress) : 0)
            .frame(width: availableWidth, alignment: rtl ? .trailing : .leading)
            .clipped()
            .onAppear { restartScrollIfNeeded() }
            .onChange(of: text) { _ in restartScrollIfNeeded() }
            .onChange(of: duration) { _ in restartScrollIfNeeded() }
            .onDisappear { scrollTimer?.invalidate(); scrollTimer = nil }
    }

    private func restartScrollIfNeeded() {
        scrollTimer?.invalidate(); scrollTimer = nil
        start = Date(); progress = 0
        guard distance > 2 else { return }
        let timer = Timer(timeInterval: 0.08, repeats: true) { _ in
            let elapsed = Date().timeIntervalSince(start)
            progress = min(1, max(0, elapsed / Double(max(duration, 1))))
            if progress >= 1 { scrollTimer?.invalidate(); scrollTimer = nil }
        }
        scrollTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }
}

private struct DotGrip: View {
    var body: some View {
        Text("••••••\n••••••")
            .font(.system(size: 4, weight: .bold, design: .rounded))
            .lineSpacing(-4)
            .foregroundStyle(.white.opacity(0.5))
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
            .help("Long press and drag to reorder")
    }
}

private struct ReorderPressHandle: NSViewRepresentable {
    let vertical: Bool
    let stepLength: CGFloat
    let onMove: (Int) -> Void

    func makeNSView(context: Context) -> ReorderView {
        let view = ReorderView()
        view.vertical = vertical
        view.stepLength = stepLength
        view.onMove = onMove
        return view
    }

    func updateNSView(_ nsView: ReorderView, context: Context) {
        nsView.vertical = vertical
        nsView.stepLength = stepLength
        nsView.onMove = onMove
    }

    final class ReorderView: NSView {
        var vertical = false
        var stepLength: CGFloat = 48
        var onMove: ((Int) -> Void)?
        private var downAt: TimeInterval = 0
        private var startPoint: NSPoint = .zero
        private var emittedSteps = 0
        private var didLongPress = false

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        override func mouseDown(with event: NSEvent) {
            downAt = ProcessInfo.processInfo.systemUptime
            startPoint = NSEvent.mouseLocation
            emittedSteps = 0
            didLongPress = false
        }

        override func mouseDragged(with event: NSEvent) {
            guard ProcessInfo.processInfo.systemUptime - downAt >= 0.55 else { return }
            didLongPress = true
            let point = NSEvent.mouseLocation
            let distance = vertical ? startPoint.y - point.y : point.x - startPoint.x
            let safeStep = max(stepLength, 1)
            let targetSteps = Int(distance / safeStep)
            while emittedSteps < targetSteps { emittedSteps += 1; onMove?(1) }
            while emittedSteps > targetSteps { emittedSteps -= 1; onMove?(-1) }
        }

        override func mouseUp(with event: NSEvent) {
            didLongPress = false
        }
    }
}

private struct FXPressHandle: NSViewRepresentable {
    let onRelease: (Bool) -> Void

    func makeNSView(context: Context) -> FXPressView {
        let view = FXPressView()
        view.onRelease = onRelease
        return view
    }

    func updateNSView(_ nsView: FXPressView, context: Context) { nsView.onRelease = onRelease }

    final class FXPressView: NSView {
        var onRelease: ((Bool) -> Void)?
        private var downAt: TimeInterval?

        override var isOpaque: Bool { false }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func draw(_ dirtyRect: NSRect) {}

        override func mouseDown(with event: NSEvent) { downAt = ProcessInfo.processInfo.systemUptime }

        override func mouseUp(with event: NSEvent) {
            guard let downAt else { return }
            self.downAt = nil
            let releasedInside = bounds.contains(convert(event.locationInWindow, from: nil))
            guard releasedInside else { return }
            onRelease?(ProcessInfo.processInfo.systemUptime - downAt >= 0.55)
        }
    }
}

private struct DragHandle: NSViewRepresentable {
    let onDragStart: () -> Void
    let onDrag: (NSPoint) -> Void
    let onDragEnd: () -> Void
    func makeNSView(context: Context) -> DragView { let view = DragView(); view.onDragStart = onDragStart; view.onDrag = onDrag; view.onDragEnd = onDragEnd; return view }
    func updateNSView(_ nsView: DragView, context: Context) { nsView.onDragStart = onDragStart; nsView.onDrag = onDrag; nsView.onDragEnd = onDragEnd }
    final class DragView: NSView {
        var onDragStart: (() -> Void)?
        var onDrag: ((NSPoint) -> Void)?
        var onDragEnd: (() -> Void)?
        private var dragStart: NSPoint = .zero
        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: bounds, options: [.activeAlways, .mouseEnteredAndExited, .mouseMoved, .inVisibleRect], owner: self, userInfo: nil))
        }
        override func mouseDown(with event: NSEvent) { dragStart = NSEvent.mouseLocation; onDragStart?() }
        override func mouseDragged(with event: NSEvent) {
            let current = NSEvent.mouseLocation
            onDrag?(NSPoint(x: current.x - dragStart.x, y: current.y - dragStart.y))
        }
        override func mouseUp(with event: NSEvent) { onDragEnd?() }
    }
}

private struct HoverPositionReader: NSViewRepresentable {
    let onMove: (CGPoint) -> Void
    func makeNSView(context: Context) -> TrackView { let view = TrackView(); view.onMove = onMove; return view }
    func updateNSView(_ nsView: TrackView, context: Context) { nsView.onMove = onMove }
    final class TrackView: NSView {
        var onMove: ((CGPoint) -> Void)?
        override func updateTrackingAreas() {
            super.updateTrackingAreas(); trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: bounds, options: [.activeAlways, .mouseMoved, .inVisibleRect], owner: self, userInfo: nil))
        }
        override func mouseMoved(with event: NSEvent) { onMove?(convert(event.locationInWindow, from: nil)) }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }
}

private extension View {
    var vertical: some View { self.frame(maxWidth: .infinity) }
}

extension Color {
    init(hex: String) {
        let raw = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: raw).scanHexInt64(&value)
        let r, g, b: Double
        if raw.count == 6 { r = Double((value >> 16) & 0xff) / 255; g = Double((value >> 8) & 0xff) / 255; b = Double(value & 0xff) / 255 }
        else { r = 0.15; g = 0.2; b = 0.27 }
        self.init(red: r, green: g, blue: b)
    }
}
