// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FloBar",
    // M1's original major release was macOS 11. Keep the native app's declared floor there;
    // opt-in APIs that arrived later are individually availability-gated.
    platforms: [.macOS(.v11)],
    products: [
        .executable(name: "FloBar", targets: ["FloBarApp"]),
        .executable(name: "FloBarGuardian", targets: ["FloBarGuardian"])
    ],
    targets: [
        .target(name: "FloBarCore", path: "Sources/FloBarCore"),
        .executableTarget(name: "FloBarApp", dependencies: ["FloBarCore"], path: "Sources/FloBarApp"),
        .executableTarget(name: "FloBarGuardian", path: "Sources/FloBarGuardian"),
        .testTarget(name: "FloBarCoreTests", dependencies: ["FloBarCore"], path: "Tests/FloBarCoreTests")
    ]
)
