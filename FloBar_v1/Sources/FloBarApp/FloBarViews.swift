import AppKit
import FloBarCore
import SwiftUI
import UniformTypeIdentifiers

struct FloBarView: View {
    @ObservedObject var model: FloBarModel
    let midi: CoreMIDIService
    let app: FloBarApplication

    var body: some View {
        let vertical = model.activeProfile.edge.isVertical
        Group {
            if vertical {
                VStack(spacing: 3) {
                    dragHandle
                    messageTile.frame(height: 116)
                    midiInputTile
                    readyAndTimer.vertical
                    ForEach(visibleButtons) { button in buttonView(button).frame(height: 46) }
                    utilityControls.vertical
                }
            } else {
                HStack(spacing: 3) {
                    dragHandle
                    messageTile.frame(width: 320)
                    ForEach(visibleButtons) { button in buttonView(button).frame(maxWidth: .infinity) }
                    midiInputTile.frame(width: 112)
                    readyAndTimer
                    utilityControls
                }
            }
        }
        .padding(4)
        .background(Color(hex: "101923").opacity(0.98))
        .clipShape(RoundedRectangle(cornerRadius: vertical ? 12 : 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: vertical ? 12 : 16).stroke(Color.white.opacity(0.17), lineWidth: 1))
        .shadow(color: .black.opacity(0.28), radius: 9, y: 3)
        .onChange(of: model.activeProfile.edge) { app.repositionBar($0) }
        .alert("FloBar", isPresented: Binding(get: { model.lastError != nil }, set: { if !$0 { model.lastError = nil } })) {
            Button("OK", role: .cancel) { model.lastError = nil }
        } message: { Text(model.lastError ?? "") }
    }

    private var visibleButtons: [BarButton] {
        model.activeProfile.buttons.filter { button in
            guard button.visible else { return false }
            if button.role == "fxMute" && !model.activeProfile.fxMuteEnabled { return false }
            if (5...12).contains(button.order) { return model.activeProfile.showEffects }
            if (0...4).contains(button.order) { return model.activeProfile.showInputsOutputs }
            return true
        }.sorted { $0.order < $1.order }
    }

    private var dragHandle: some View {
        DragHandle(onDragStart: { app.beginPanelDrag() }, onDrag: { delta in app.movePanel(by: delta) })
            .frame(width: 13, height: 28)
            .overlay(Image(systemName: "line.3.horizontal").rotationEffect(model.activeProfile.edge.isVertical ? .degrees(90) : .zero).foregroundStyle(.white.opacity(0.42)).font(.system(size: 11, weight: .medium)))
            .help("Drag FloBar to reposition")
    }

    private var messageTile: some View {
        MessageTile(model: model, app: app)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(hex: "1B2935"))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.white.opacity(0.12), lineWidth: 1))
    }

    private func buttonView(_ button: BarButton) -> some View {
        let feedback = model.stateColors[button.command.number]
        let fill = feedback == "on" ? Color(hex: button.activeColor) : feedback == "off" ? Color(hex: button.inactiveColor) : Color(hex: "263443")
        return Button {
            if ["play", "loop", "goBack", "hide", "split"].contains(button.role) {
                app.activateBarButton(button)
            } else {
                sendButtonCommand(button, confirmedState: feedback)
            }
        } label: {
            Text(button.shortTitle).font(.system(size: model.activeProfile.edge.isVertical ? 14 : 17, weight: .bold, design: .rounded))
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
            Text("MIDI INPUT").font(.system(size: 9, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.55))
            Text(model.displayIACText ? (model.pairedIACText ?? model.currentMIDIText) : model.currentMIDIText)
                .font(.system(size: 13, weight: .bold, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.62)
                .foregroundStyle(.white.opacity(0.92))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "1B2935"))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .help("Most recent controller event and its paired IAC output")
    }

    private var readyAndTimer: some View {
        HStack(spacing: 4) {
            VStack(spacing: 1) {
                Text(model.activeMessageSet.readyText).font(.system(size: 16, weight: .heavy, design: .rounded)).lineLimit(1).minimumScaleFactor(0.6)
                Text(model.activeMessageSet.name).font(.system(size: 9, weight: .medium)).lineLimit(1).minimumScaleFactor(0.6).foregroundStyle(.white.opacity(0.72))
            }
            .frame(minWidth: 52, maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(hex: model.activeMessageSet.readyColor))
            .clipShape(RoundedRectangle(cornerRadius: 6))
            Button { model.startOrPauseTimer() } label: {
                VStack(spacing: 1) {
                    Image(systemName: model.timerRunning ? "pause.fill" : "play.fill").font(.system(size: 13, weight: .bold))
                    Text(timerString).font(.system(size: 11, weight: .bold, design: .monospaced))
                }
                .frame(width: 53, height: 48).background(Color(hex: "303C49")).clipShape(RoundedRectangle(cornerRadius: 6))
                .foregroundStyle(model.timerOvertime ? Color.orange : .white)
            }.buttonStyle(.plain).help("Start or pause the show timer")
            Button { model.resetTimer() } label: {
                Image(systemName: "arrow.counterclockwise").font(.system(size: 13, weight: .bold))
                    .frame(width: 28, height: 48).background(Color(hex: "303C49")).clipShape(RoundedRectangle(cornerRadius: 6)).foregroundStyle(.white.opacity(0.85))
            }.buttonStyle(.plain).help("Reset show timer")
        }.frame(height: 48)
    }

    private var timerString: String {
        let seconds = max(0, Int(model.timerRemaining))
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private var utilityControls: some View {
        HStack(spacing: 3) {
            VStack(spacing: 2) {
                Text("BPM").font(.system(size: 13, weight: .heavy, design: .rounded)).foregroundStyle(Color(hex: "E6C778"))
                Text("—").font(.system(size: 11, weight: .medium, design: .monospaced)).foregroundStyle(.white.opacity(0.7))
            }.frame(width: 49, height: 48).background(Color(hex: "263443")).clipShape(RoundedRectangle(cornerRadius: 6))
                .help("External BPM source is reserved for a future extension")
        }
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
                .background(disconnected ? Color(hex: "75431D") : Color.clear)
            Spacer(minLength: 1)
            Text(battery.map { "\($0)%" } ?? "--%")
                .foregroundStyle(battery == nil ? Color.white.opacity(0.38) : Color.white.opacity(0.78))
        }
        .font(.system(size: 10, weight: .bold, design: .rounded))
        .padding(.horizontal, 4).frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: "304250"))
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

    private var textWidth: CGFloat {
        (text as NSString).size(withAttributes: [.font: NSFont.systemFont(ofSize: 17, weight: .bold)]).width
    }
    private var distance: CGFloat { max(0, textWidth - availableWidth) }
    private var rtl: Bool { primaryLanguageDirection(text) }

    var body: some View {
        let scrolls = distance > 2
        TimelineView(.animation(minimumInterval: 0.08, paused: !scrolls)) { context in
            let progress = scrolls ? min(1, max(0, context.date.timeIntervalSince(start) / Double(max(duration, 1)))) : 0
            Text(text)
                .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                .frame(width: max(textWidth, availableWidth), alignment: rtl ? .trailing : .leading)
                .offset(x: scrolls ? (rtl ? distance * progress : -distance * progress) : 0)
                .frame(width: availableWidth, alignment: rtl ? .trailing : .leading)
                .clipped()
        }
        .onChange(of: text) { _ in start = Date() }
    }
}

private struct DragHandle: NSViewRepresentable {
    let onDragStart: () -> Void
    let onDrag: (NSPoint) -> Void
    func makeNSView(context: Context) -> DragView { let view = DragView(); view.onDragStart = onDragStart; view.onDrag = onDrag; return view }
    func updateNSView(_ nsView: DragView, context: Context) { nsView.onDragStart = onDragStart; nsView.onDrag = onDrag }
    final class DragView: NSView {
        var onDragStart: (() -> Void)?
        var onDrag: ((NSPoint) -> Void)?
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
