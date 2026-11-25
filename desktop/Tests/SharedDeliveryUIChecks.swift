import AppKit
import ObjectiveC.runtime
import SwiftUI

@main
@MainActor
struct SharedDeliveryUIChecks {
    static let android = "fixture/android/1.0.12/108/internal"
    static let ios = "fixture/ios/1.0.12/108/production"

    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    static func main() {
        guard let folder = ProcessInfo.processInfo.environment["STOREGATE_WORKSPACE_DIR"] else { exit(2) }
        let workspace = URL(fileURLWithPath: folder, isDirectory: true)
        let result = workspace.appendingPathComponent("ui-result.txt")
        guard let board = isolatedPasteboard() else {
            try? "隔離したクリップボードを準備できません".write(to: result, atomically: true, encoding: .utf8)
            exit(2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let window = NSWindow(contentRect: NSRect(x: 50, y: 50, width: 1120, height: 1020),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "StoreGate Delivery UI Check"
        window.contentView = NSHostingView(rootView: SharedDeliveryView(workspace: workspace, openMac: {}))
        window.makeKeyAndOrderFront(nil)
        app.activate()
        Task {
            do {
                try await check(window, workspace: workspace, board: board)
                try "pass".write(to: result, atomically: true, encoding: .utf8)
            } catch {
                saveScreenshot(window, to: workspace.appendingPathComponent("ui-failure.png"))
                try? String(describing: error).write(to: result, atomically: true, encoding: .utf8)
            }
            app.terminate(nil)
        }
        app.run()
    }

    private static func saveScreenshot(_ window: NSWindow, to path: URL) {
        guard let view = (window.attachedSheet ?? window).contentView,
              let image = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: image)
        try? image.representation(using: .png, properties: [:])?.write(to: path, options: .atomic)
    }

    // Override only this test process. No operation reaches the user's normal pasteboard.
    private static func isolatedPasteboard() -> NSPasteboard? {
        let board = NSPasteboard.withUniqueName()
        let selector = NSSelectorFromString("generalPasteboard")
        guard let getter = class_getClassMethod(NSPasteboard.self, selector) else { return nil }
        let replacement: @convention(block) (AnyObject) -> NSPasteboard = { _ in board }
        method_setImplementation(getter, imp_implementationWithBlock(replacement))
        return NSPasteboard.general.name == board.name ? board : nil
    }

    private static func check(_ window: NSWindow, workspace: URL, board: NSPasteboard) async throws {
        _ = try await anchor("test-copy")
        try await Task.sleep(for: .milliseconds(600))
        try await click("test-copy", in: window)
        let copied = board.string(forType: .string) ?? ""
        guard copied.contains("1.0.12（ビルド 108）"),
              copied.contains("https://play.google.com/apps/testing/org.example.fixture") else {
            throw Failure(description: "端末向け案内に対象版と参加リンクがありません")
        }
        try await click("test-stage-availability", in: window)
        try await type("Android QA", at: "test-device", in: window)
        try await type("配布ページが開いた", at: "test-note", in: window)
        try await save(in: window)
        try await click("test-stage-qa", in: window)
        try await type("PDFを開く", at: "test-operation", in: window)
        try await type("合成PDFを表示できた", at: "test-note", in: window)
        try await save(in: window)
        let first = try testing(workspace, stage: "qa").first
        guard let id = first?["id"] as? Int else { throw Failure(description: "QAを読み戻せません") }
        try await click("test-observation-\(id)", in: window, disclosure: true)
        try await click("test-rerecord-\(id)", in: window)
        try await type("再起動後も表示できた", at: "test-note", in: window)
        try await save(in: window)

        let qa = try testing(workspace, stage: "qa")
        let availability = try testing(workspace, stage: "availability")
        let history = try testing(workspace, stage: nil)
        guard qa.first?["note"] as? String == "再起動後も表示できた",
              qa.first?["build_source"] as? String == "store",
              availability.first?["outcome"] as? String == "pass",
              history.contains(where: { $0["stage"] as? String == "availability" && $0["outcome"] as? String == "blocked" }),
              history.contains(where: { $0["id"] as? Int == id }) else {
            throw Failure(description: "最新の結果、履歴、ビルドの確認元を読み戻せません")
        }

        try await click("release-\(ios)", in: window)
        let unsaved = try cli(workspace, ["intake", ios])["intake"] as? [String: Any]
        guard unsaved?["saved_at"] is NSNull else { throw Failure(description: "既存案件に開始時の案内がありません") }
        try await click("intake-edit", in: window)
        try await type("JPY 400", at: "intake-price", in: window, replacing: true)
        try await click("intake-save", in: window)
        try await waitForSheetToClose(window)
        let saved = try cli(workspace, ["intake", ios])["intake"] as? [String: Any]
        guard saved?["saved_at"] is String,
              (saved?["fields"] as? [[String: Any]])?.contains(where: {
                  $0["key"] as? String == "price" && $0["value"] as? String == "JPY 400"
              }) == true else {
            throw Failure(description: "GUIで保存した価格をCLIで読み戻せません")
        }
        try await click("intake-edit", in: window)
        let price = try await anchor("intake-price")
        guard price.window === window.attachedSheet else { throw Failure(description: "保存した確認リストを開けません") }
        try await closeSheet(window)
        try await click("preflight-open", in: window)
        _ = try await anchor("preflight-content")
        let preflight = try cli(workspace, ["preflight", ios])["preflight"] as? [String: Any]
        guard (preflight?["checks"] as? [[String: Any]])?.contains(where: {
            $0["name"] as? String == "authentication" && $0["outcome"] as? String == "reference_missing"
        }) == true else { throw Failure(description: "認証参照の未設定を照合できません") }
        try await closeSheet(window)
        try await click("approval-packet", in: window)
        _ = try await anchor("packet-content")
        try await click("packet-baseline", in: window)
        let stored = try cli(workspace, ["approval-packet", ios])["approval_packet"] as? [String: Any]
        guard stored?["baseline_at"] is String else {
            throw Failure(description: "確認票の比較基準が保存されていません")
        }
    }

    private static func anchor(_ id: String) async throws -> NSView {
        for _ in 0..<70 {
            if let view = DeliveryCheckAnchor.views[id], view.window != nil,
               view.bounds.width > 0 && view.bounds.height > 0 { return view }
            try await Task.sleep(for: .milliseconds(80))
        }
        throw Failure(description: "UI項目が見つかりません \(id)")
    }

    private static func click(_ id: String, in window: NSWindow, disclosure: Bool = false) async throws {
        let view = try await anchor(id)
        guard view.window === (window.attachedSheet ?? window) else {
            throw Failure(description: "UI項目が操作対象のウィンドウにありません \(id)")
        }
        let x = disclosure ? view.bounds.minX + 5 : view.bounds.midX
        let location = view.convert(NSPoint(x: x, y: view.bounds.midY), to: nil)
        let number = (window.attachedSheet ?? window).windowNumber
        print("UI click \(id) at \(location)")
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            guard let event = NSEvent.mouseEvent(with: type, location: location, modifierFlags: [],
                                                 timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: number,
                                                 context: nil, eventNumber: 0, clickCount: 1, pressure: 1) else {
                throw Failure(description: "UIイベントを作れません \(id)")
            }
            NSApplication.shared.postEvent(event, atStart: false)
        }
        try await Task.sleep(for: .milliseconds(600))
    }

    private static func type(_ text: String, at id: String, in window: NSWindow, replacing: Bool = false) async throws {
        try await click(id, in: window)
        guard let field = window.attachedSheet?.firstResponder as? NSTextView else {
            throw Failure(description: "入力欄を開けません \(id)")
        }
        if replacing { field.selectAll(nil) }
        field.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
    }

    private static func save(in window: NSWindow) async throws {
        try await click("test-save", in: window)
        try await waitForSheetToClose(window)
    }

    private static func closeSheet(_ window: NSWindow) async throws {
        try await click("release-sheet-close", in: window)
        try await waitForSheetToClose(window)
    }

    private static func waitForSheetToClose(_ window: NSWindow) async throws {
        for _ in 0..<70 {
            if window.attachedSheet == nil { return }
            try await Task.sleep(for: .milliseconds(80))
        }
        throw Failure(description: "保存後にシートが閉じません")
    }

    private static func testing(_ workspace: URL, stage: String?) throws -> [[String: Any]] {
        let result = try cli(workspace, ["test-status", android])["testing"] as? [String: Any]
        if let stage, let stages = result?["stages"] as? [String: [[String: Any]]] {
            return stages[stage] ?? []
        }
        return result?["history"] as? [[String: Any]] ?? []
    }

    private static func cli(_ workspace: URL, _ argv: [String]) throws -> [String: Any] {
        guard let script = Bundle.main.resourceURL?.appendingPathComponent("StoreGateTools/delivery/storegate.py") else {
            throw Failure(description: "検証用の配信エンジンがありません")
        }
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", script.path, "--workspace", workspace.path] + argv
        process.standardOutput = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw Failure(description: "配信エンジンを読み戻せません \(argv.first ?? "")")
        }
        return result
    }
}
