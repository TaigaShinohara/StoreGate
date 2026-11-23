import AppKit
import SwiftUI
import UniformTypeIdentifiers

private struct SharedImage: Decodable, Identifiable, Sendable {
    var id: String { "\(locale)/\(slot)/\(order)" }
    let path: String
    let slot: String
    let locale: String
    let order: Int
}

private struct SharedCheck: Decodable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let state: String
    let message: String

    var symbol: String {
        switch state {
        case "pass": "checkmark.circle"
        case "blocked": "exclamationmark.triangle"
        case "not_checked": "questionmark.circle"
        case "stale": "arrow.trianglehead.clockwise"
        default: "circle"
        }
    }
}

private struct SharedOperation: Decodable, Identifiable, Sendable {
    var id: String { "\(action)/\(time)" }
    let action: String
    let state: String
    let time: String
    let remoteState: String?

    var title: String {
        ["store-status": "ストア照会", "submit": "審査送信", "publish": "本番公開",
         "upload-build": "ビルド投入", "play-edit": "Playテストトラックの編集"][action] ?? action
    }

    var status: String {
        remoteState ?? ["running": "実行中", "unknown": "結果未確定",
                        "requested": "リクエスト済み", "received": "ストアで受理を確認",
                        "verified": "読み戻し確認済み", "rejected": "拒否",
                        "validated": "編集を検証済み"][state] ?? state
    }
}

private struct SharedRelease: Decodable, Identifiable, Sendable {
    let id: String
    let app: String
    let platform: String
    let version: String
    let build: String
    let next: String?
    let phase: String?
    let checks: [SharedCheck]?
    let images: [SharedImage]?
    let console: URL?
    let operations: [SharedOperation]?

    var platformTitle: String {
        switch platform {
        case "ios": "iPhone・iPad"
        case "android": "Google Play"
        default: "Mac App Store"
        }
    }
}

private struct SharedResponse: Decodable, Sendable {
    let releases: [SharedRelease]?
    let legacy: [SharedRelease]?
    let error: String?
    let confirmation: String?
    let migrationError: String?
    let checks: [SharedCheck]?
}

@MainActor
struct SharedDeliveryView: View {
    let workspace: URL
    let openMac: () -> Void
    @State private var releases: [SharedRelease] = []
    @State private var legacy: [SharedRelease] = []
    @State private var selectedID: String?
    @State private var busy = false
    @State private var message: String?
    @State private var pending: Approval?
    @State private var showEvidence = false
    @State private var evidenceCheck = "account"
    @State private var evidenceNote = ""

    private struct Approval: Identifiable {
        var id: String { digest }
        let arguments: [String]
        let digest: String
        let text: String
    }

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 12) {
                Text("配信案件").font(.title2.bold()).accessibilityAddTraits(.isHeader)
                Menu("登録", systemImage: "plus") {
                    Button("アプリのプロファイル…") { importFile(command: "register") }
                    Button("版の配信設定…") { importFile(command: "release") }
                }
                .disabled(busy)
                List(selection: $selectedID) {
                    ForEach(releases) { release in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(release.app).font(.headline)
                            Text("\(release.platformTitle) · \(release.version) (\(release.build))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .tag(release.id)
                    }
                    if !legacy.isEmpty {
                        Section("既存のMac App Store案件") {
                            ForEach(legacy) { record in
                                Button(record.app, action: openMac)
                                    .buttonStyle(.plain)
                            }
                        }
                    }
                }
                .accessibilityLabel("配信するアプリと版")
                HStack {
                    Button("更新", systemImage: "arrow.clockwise") { perform(["list"]) }
                    Button("一括準備") { perform(["prepare"]) }
                }
                .disabled(busy)
            }
            .padding(16)
            .frame(minWidth: 250, idealWidth: 290)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if busy { ProgressView().controlSize(.small).accessibilityLabel("確認中") }
                    if let message { Text(message).font(.callout).textSelection(.enabled) }
                    if let release = releases.first(where: { $0.id == selectedID }) {
                        details(release)
                    } else {
                        Text("審査に出す版を選ぶ").font(.title2.bold())
                        Text("アプリと配信設定を登録すると、署名・掲載素材・確認結果をストアごとに表示します。")
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(24)
            }
            .frame(minWidth: 485, maxWidth: .infinity, maxHeight: .infinity)
        }
        .task { perform(["list"]) }
        .sheet(item: $pending) { item in
            VStack(alignment: .leading, spacing: 12) {
                Text(item.arguments[0] == "submit" ? "審査送信の内容を確認" : "本番公開の内容を確認").font(.title2.bold())
                ScrollView { Text(item.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                HStack {
                    Spacer()
                    Button("戻る", role: .cancel) { pending = nil }
                    Button("この内容で実行") {
                        pending = nil
                        perform(item.arguments + ["--approve", item.digest])
                    }.buttonStyle(.borderedProminent)
                }
            }
            .padding(22)
            .frame(width: 690, height: 520)
        }
        .sheet(isPresented: $showEvidence) {
            VStack(alignment: .leading, spacing: 14) {
                Text("対象版の確認結果を記録").font(.headline)
                Picker("確認項目", selection: $evidenceCheck) {
                    Text("アカウント・権限").tag("account")
                    Text("実機での確認").tag("device")
                    Text("担当者による申告").tag("declarations")
                    Text("提出前チェックリスト").tag("checklist")
                    Text("ストア保存内容との照合").tag("storefront")
                }
                TextField("確認した内容と根拠", text: $evidenceNote, axis: .vertical)
                    .lineLimit(3...5)
                HStack {
                    Button("キャンセル") { showEvidence = false }
                    Button("確認済みとして保存") {
                        guard let selectedID else { return }
                        perform(["attest", selectedID, evidenceCheck, "--note", evidenceNote])
                        showEvidence = false
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(evidenceNote.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding(22)
            .frame(width: 460)
        }
    }

    @ViewBuilder private func details(_ release: SharedRelease) -> some View {
        Text(release.app).font(.largeTitle.bold()).accessibilityAddTraits(.isHeader)
        Text("\(release.platformTitle)　\(release.version)（ビルド \(release.build)）")
            .foregroundStyle(.secondary)
        Text(release.next ?? "").font(.headline)
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button("監査・準備") { perform(["prepare", release.id]) }.buttonStyle(.borderedProminent)
                Button("ビルド") { perform(["build", release.id]) }
                Button("ストア照会") { perform(["store-status", release.id]) }
                Button("確認を記録…") { evidenceNote = ""; showEvidence = true }
            }
            HStack {
                Menu("対象版の操作") {
                    Button("実機QAを実行") { perform(["qa", release.id]) }
                    Button("CIのQA結果を取り込む…") { importFile(command: "import-qa", releaseID: release.id) }
                    Button("再撮影") { perform(["capture", release.id]) }
                    Button("掲載情報と画像の下書き") { perform(["draft", release.id]) }
                    Button("提出用ビルドの投入・照合") { perform(["upload-build", release.id]) }
                    Button("審査の下書きを用意") { perform(["review-draft", release.id]) }
                    Button("審査送信の内容を表示…") { perform(["submit", release.id]) }
                    Button("本番公開の内容を表示…") { perform(["publish", release.id]) }
                }
                if let console = release.console { Link("公式画面を開く", destination: console) }
            }
        }.disabled(busy)
        ForEach(release.checks ?? []) { check in
            DisclosureGroup {
                Text(check.message).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            } label: {
                Label(checkTitle(check.name), systemImage: check.symbol)
            }
        }
        if let operations = release.operations, !operations.isEmpty {
            DisclosureGroup("ストア操作の履歴") {
                ForEach(operations) { item in
                    LabeledContent(item.title, value: item.status)
                }
            }
        }
        if let images = release.images, !images.isEmpty {
            Text("掲載画像").font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 12)], spacing: 12) {
                ForEach(images) { item in
                    VStack {
                        if let image = NSImage(contentsOfFile: item.path) {
                            Image(nsImage: image).resizable().scaledToFit().frame(height: 106)
                                .accessibilityLabel("\(item.locale) \(item.slot) \(item.order)枚目")
                        }
                        Text("\(item.locale) · \(item.slot) · \(item.order)").font(.caption)
                    }
                }
            }
        }
    }

    private func checkTitle(_ name: String) -> String {
        [
            "conditions": "配信条件", "screenshots": "掲載画像", "artifact": "署名付き成果物",
            "build_preflight": "配布設定", "source": "ソース監査", "metadata": "掲載原稿",
            "account": "アカウントと権限", "device": "対象版の実機確認", "declarations": "申告",
            "checklist": "提出前チェックリスト", "storefront": "ストアとの照合",
            "qa": "登録済みQA", "publication": "公開確認"
        ][name] ?? name
    }

    private func importFile(command: String, releaseID: String? = nil) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.begin { response in
            if response == .OK, let url = panel.url {
                perform([command] + (releaseID.map { [$0] } ?? []) + [url.path])
            }
        }
    }

    private func perform(_ arguments: [String]) {
        guard !busy else { return }
        busy = true
        message = nil
        let script = Bundle.main.resourceURL?
            .appendingPathComponent("StoreGateTools/delivery/storegate.py").path ?? ""
        let folder = workspace.path
        Task {
            defer { busy = false }
            do {
                let first = try await Task.detached { try Self.call(script, folder, arguments) }.value
                let decoded = try JSONDecoder().decode(SharedResponse.self, from: first)
                message = decoded.error ?? decoded.migrationError
                if let digest = decoded.confirmation {
                    let document = try JSONSerialization.jsonObject(with: first) as? [String: Any]
                    let data = try JSONSerialization.data(withJSONObject: document?["summary"] ?? [:],
                                                          options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
                    pending = Approval(arguments: arguments, digest: digest, text: String(decoding: data, as: UTF8.self))
                } else if ["store-status", "review-draft", "draft", "upload-build"].contains(arguments[0]) ||
                            (["submit", "publish"].contains(arguments[0]) && decoded.error == nil) {
                    message = decoded.error ?? String(decoding: first, as: UTF8.self)
                }
                let list: SharedResponse
                if arguments[0] == "list" {
                    list = decoded
                } else {
                    let result = try await Task.detached { try Self.call(script, folder, ["list"]) }.value
                    list = try JSONDecoder().decode(SharedResponse.self, from: result)
                }
                releases = list.releases ?? []
                legacy = list.legacy ?? []
                if selectedID == nil || !releases.contains(where: { $0.id == selectedID }) {
                    selectedID = releases.first?.id
                }
            } catch {
                message = "配信エンジンを読み込めませんでした。Python 3と保存先を確認してください。"
            }
        }
    }

    private nonisolated static func call(_ script: String, _ folder: String, _ arguments: [String]) throws -> Data {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", script, "--workspace", folder] + arguments
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        let result = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return result
    }
}
