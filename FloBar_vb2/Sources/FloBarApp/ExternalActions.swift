import AppKit
import ApplicationServices
import Foundation

final class ExternalActions {
    enum FocusResult: Equatable { case focused, missing, denied }
    private let automationQueue = DispatchQueue(label: "FloBar.ExternalAutomation", qos: .userInitiated)

    func activateRunningApp(named name: String) -> FocusResult {
        let escaped = name.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        guard let check = NSAppleScript(source: "tell application \"System Events\" to (name of processes) contains \"\(escaped)\"") else { return .denied }
        var error: NSDictionary?
        let result = check.executeAndReturnError(&error)
        guard error == nil else { return .denied }
        guard result.booleanValue else { return .missing }
        guard let activate = NSAppleScript(source: "tell application \"\(escaped)\" to activate") else { return .denied }
        error = nil
        _ = activate.executeAndReturnError(&error)
        return error == nil ? .focused : .denied
    }

    func setFrontmostProcess(_ name: String) -> Bool {
        let escaped = name.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        var error: NSDictionary?
        guard let script = NSAppleScript(source: "tell application \"System Events\" to tell process \"\(escaped)\" to set frontmost to true") else { return false }
        _ = script.executeAndReturnError(&error)
        return error == nil
    }

    func sendKeystroke(_ key: String, toProcess process: String) -> Bool {
        let escapedKey = key.replacingOccurrences(of: "\"", with: "\\\"")
        let escapedProcess = process.replacingOccurrences(of: "\"", with: "\\\"")
        var error: NSDictionary?
        guard let script = NSAppleScript(source: "tell application \"System Events\" to tell process \"\(escapedProcess)\" to keystroke \"\(escapedKey)\"") else { return false }
        _ = script.executeAndReturnError(&error)
        return error == nil
    }

    func sendKeystrokeAsync(_ key: String, toProcess process: String, completion: @escaping (Bool) -> Void) {
        automationQueue.async { completion(self.sendKeystroke(key, toProcess: process)) }
    }

    func switchApplicationsAsync() {
        automationQueue.async { self.switchApplications() }
    }

    func switchApplications() {
        var error: NSDictionary?
        guard let script = NSAppleScript(source: "tell application \"System Events\" to key code 48 using {command down}") else { return }
        _ = script.executeAndReturnError(&error)
    }

    func sendArrowUpToFrontmostApp() -> Bool {
        guard AXIsProcessTrusted() else { return false }
        let down = CGEvent(keyboardEventSource: nil, virtualKey: 126, keyDown: true)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: 126, keyDown: false)
        down?.post(tap: .cghidEventTap); up?.post(tap: .cghidEventTap)
        return down != nil && up != nil
    }

    func setJamZoneLayout(split: Bool, screenWidth: Int, screenHeight: Int) -> Bool {
        let width = split ? 1 : max(1, screenWidth)
        let script = """
        tell application "System Events"
            tell process "Jamzone"
                set currentSize to size of window 1
                set newHeight to item 2 of currentSize
                set size of window 1 to {\(width), newHeight}
                set position of window 1 to {0, \(screenHeight) - newHeight}
            end tell
        end tell
        """
        var error: NSDictionary?
        guard let appleScript = NSAppleScript(source: script) else { return false }
        _ = appleScript.executeAndReturnError(&error)
        return error == nil
    }

    func optionLeftIfJamZoneFront() -> Bool {
        guard NSWorkspace.shared.frontmostApplication?.localizedName?.localizedCaseInsensitiveContains("Jamzone") == true else { return false }
        guard AXIsProcessTrusted() else { return false }
        let down = CGEvent(keyboardEventSource: nil, virtualKey: 123, keyDown: true)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: 123, keyDown: false)
        down?.flags = .maskAlternate
        up?.flags = .maskAlternate
        down?.post(tap: .cghidEventTap)
        up?.post(tap: .cghidEventTap)
        return down != nil && up != nil
    }

    func requestAccessibilityIfOptedIn() -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    func requestListenEventAccess() -> Bool {
        if CGPreflightListenEventAccess() { return true }
        return CGRequestListenEventAccess()
    }
}

final class GlobalInputMonitor {
    var onSpecialKey: ((String) -> Void)?
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?

    func startIfAuthorized() -> Bool {
        guard tap == nil else { return true }
        let mask = CGEventMask(1) << CGEventType.keyDown.rawValue
        let callback: CGEventTapCallBack = { _, type, event, refcon in
            guard type == .keyDown, let refcon else { return Unmanaged.passUnretained(event) }
            let monitor = Unmanaged<GlobalInputMonitor>.fromOpaque(refcon).takeUnretainedValue()
            var length = 8
            var buffer = [UniChar](repeating: 0, count: length)
            event.keyboardGetUnicodeString(maxStringLength: length, actualStringLength: &length, unicodeString: &buffer)
            if length > 0 {
            let key = String(decoding: buffer.prefix(length), as: UTF16.self)
                if key == "§" || key == "↑" || key == "↓" {
                    DispatchQueue.main.async { monitor.onSpecialKey?(key == "↑" ? "Up" : key == "↓" ? "Down" : key) }
                }
            }
            return Unmanaged.passUnretained(event)
        }
        guard let created = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                             eventsOfInterest: mask, callback: callback,
                                             userInfo: Unmanaged.passUnretained(self).toOpaque()) else { return false }
        tap = created
        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, created, 0)
        if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes) }
        CGEvent.tapEnable(tap: created, enable: true)
        return true
    }

    func stop() {
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CGEvent.tapEnable(tap: tap, enable: false) }
        source = nil; tap = nil
    }
}
