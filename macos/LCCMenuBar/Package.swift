// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "LCCMenuBar",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "LCCMenuBar", targets: ["LCCMenuBar"]),
    ],
    targets: [
        .executableTarget(
            name: "LCCMenuBar",
            path: "Sources/LCCMenuBar"
        ),
    ]
)

