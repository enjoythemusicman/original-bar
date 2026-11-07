import XCTest
@testable import FloBarCore

final class DomainTests: XCTestCase {
    func testApprovedStartupSyncIsExactTwoPassSequence() {
        let sequence = StartupSyncEvent.approvedSequence
        XCTAssertEqual(sequence.count, 16)
        XCTAssertEqual(Array(sequence.prefix(8)), Array(sequence.suffix(8)))
        XCTAssertEqual(sequence.prefix(8).map(\.number), [42, 45, 41, 39, 40, 22, 18, 43])
        XCTAssertEqual(sequence.prefix(8).map(\.kind), [.controlChange, .controlChange, .controlChange, .controlChange, .controlChange, .noteOn, .noteOn, .controlChange])
        XCTAssertTrue(sequence.allSatisfy { $0.channel == 15 && $0.value == 127 })
        XCTAssertEqual(sequence.filter { $0.kind == .noteOn }.map(\.number), [22, 18, 22, 18])
    }

    func testMIDIInputDisplayOmitsChannelAndNamesCCValue() {
        XCTAssertEqual(MIDIEvent.cc(channel: 16, number: 43, value: 127).rawLabel, "cc43v127")
        XCTAssertEqual(MIDIEvent.cc(channel: 1, number: 15, value: 0).rawLabel, "cc15v0")
    }

    func testLanguageDirectionUsesFirstLetterScript() {
        XCTAssertTrue(primaryLanguageDirection("הודעה באנגלית later"))
        XCTAssertFalse(primaryLanguageDirection("English הודעה"))
        XCTAssertFalse(primaryLanguageDirection("🎸🎤"))
    }

    func testMessageLimitCountsGraphemeClusters() {
        XCTAssertTrue(validMessageText(String(repeating: "🎸", count: 40)))
        XCTAssertFalse(validMessageText(String(repeating: "🎸", count: 41)))
        XCTAssertTrue(validMessageText("🇮🇱"))
    }

    func testDefaultsDoNotEmitRemovedCCsOrUnmappedCCZero() {
        let profile = BarProfile()
        let mapped = profile.buttons.map(\.command.number)
        XCTAssertFalse(mapped.contains(0))
        XCTAssertFalse(mapped.contains(57))
        XCTAssertFalse(mapped.contains(60))
        XCTAssertTrue(StartupSyncEvent.approvedSequence.allSatisfy { $0.number != 0 && $0.number != 57 && $0.number != 60 })
        XCTAssertTrue(StartupSyncEvent.approvedSequence.contains { $0.kind == .controlChange && $0.number == 43 })
    }

    func testTT1TimingDefaultsMatchApprovedSequence() {
        let tt1 = BarProfile().tt1
        XCTAssertEqual(tt1.trigger.channel, 16)
        XCTAssertEqual(tt1.trigger.number, 44)
        XCTAssertEqual(tt1.specialWindowSeconds, 5)
        XCTAssertEqual(tt1.fullFadeSeconds, 15)
        XCTAssertEqual(tt1.quickFadeSeconds, 1)
        XCTAssertEqual(tt1.downPlayDelaySeconds, 1.5)
        XCTAssertEqual(tt1.sidechainRestoreSeconds, 7)
        XCTAssertEqual(tt1.fullVolumeRestoreSeconds, 30)
        XCTAssertEqual(tt1.repeatWindowSeconds, 2)
        XCTAssertTrue(tt1.renewRepeatWindowOnMatch)
        XCTAssertEqual(tt1.cancelLockoutSeconds, 2.5)
    }

    func testConfigurationRoundTripPreservesOptionalLegacyImageAndTT1() throws {
        var configuration = FloBarConfiguration()
        configuration.messageSets[0].rows[0] = MessageRow(text: "תזכורת", seconds: 17, global: false, show: true, image: "legacy.png")
        configuration.profiles[0].tt1.downPlayDelaySeconds = 1.75
        let encoded = try JSONEncoder().encode(configuration)
        let decoded = try JSONDecoder().decode(FloBarConfiguration.self, from: encoded)
        XCTAssertEqual(decoded, configuration)
        XCTAssertEqual(decoded.messageSets[0].rows[0].image, "legacy.png")
        XCTAssertEqual(decoded.profiles[0].tt1.downPlayDelaySeconds, 1.75)
    }

    func testMessageRowsAlwaysHaveIndependentStableIdentifiers() throws {
        let shared = MessageRow(text: "shared")
        let set = MessageSet(name: "Independent", rows: Array(repeating: shared, count: 10))
        XCTAssertEqual(Set(set.rows.map(\.id)).count, 10)

        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(set)) as? [String: Any])
        var rows = try XCTUnwrap(object["rows"] as? [[String: Any]])
        let sharedID = try XCTUnwrap(rows.first?["id"])
        for index in rows.indices { rows[index]["id"] = sharedID }
        object["rows"] = rows
        let malformedLegacyData = try JSONSerialization.data(withJSONObject: object)
        let decoded = try JSONDecoder().decode(MessageSet.self, from: malformedLegacyData)
        XCTAssertEqual(Set(decoded.rows.map(\.id)).count, decoded.rows.count)
        XCTAssertEqual(decoded.rows.map(\.text), Array(repeating: "shared", count: 10))
    }

    func testBarProfileMigratesLayoutFieldsWithDefaults() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(BarProfile())) as? [String: Any])
        object.removeValue(forKey: "layoutOrder")
        object.removeValue(forKey: "compactSections")
        object.removeValue(forKey: "docked")
        object.removeValue(forKey: "floatingFrame")
        let migrated = try JSONDecoder().decode(BarProfile.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(migrated.layoutOrder, BarProfile.defaultLayoutOrder)
        XCTAssertFalse(migrated.compactSections)
        XCTAssertTrue(migrated.docked)
        XCTAssertNil(migrated.floatingFrame)
    }
}
