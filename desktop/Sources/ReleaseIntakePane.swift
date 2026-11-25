import SwiftUI

struct ReleaseIntake: Decodable, Identifiable, Sendable {
    struct Field: Decodable, Identifiable, Sendable {
        struct Option: Decodable, Sendable {
            let value: String
            let label: String
        }
        var id: String { key }
        let key: String
        let group: String
        let title: String
        let hint: String
        let value: String
        let state: String
        let options: [Option]

        var status: String? {
            ["prefilled": "登録設定から入力済み", "missing": "未記入", "undecided": "未定",
             "changed": "登録設定が変更されました"][state]
        }
    }

    let id: String
    let revision: String
    let saved_at: String?
    let fields: [Field]

    var needsReview: Bool { fields.contains { $0.state == "changed" } }
}

struct ReleaseIntakePane: View {
    let intake: ReleaseIntake
    let scope: String
    let platform: String
    let busy: Bool
    let error: String?
    let close: () -> Void
    let save: ([String: String]) -> Void
    @State private var answers: [String: String]

    init(intake: ReleaseIntake, scope: String, platform: String, busy: Bool, error: String?, close: @escaping () -> Void,
         save: @escaping ([String: String]) -> Void) {
        self.intake = intake
        self.scope = scope
        self.platform = platform
        self.busy = busy
        self.error = error
        self.close = close
        self.save = save
        _answers = State(initialValue: Dictionary(uniqueKeysWithValues: intake.fields.map { ($0.key, $0.value) }))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("開始時の確認リスト").font(.title2.bold())
            Text(scope).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text("今回の対応プラットフォームは合っていますか？").font(.headline)
                Text(platform).storeGateCheckID("intake-platform")
            }
            Text("分かる範囲を保存できます。送信・配信開始・公開は、対象の操作ごとに承認します。")
                .font(.callout).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(["conditions", "declarations", "testing", "devices"], id: \.self) { group in
                        let fields = intake.fields.filter { $0.group == group }
                        if !fields.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(["conditions": "配信条件", "declarations": "本人の回答が必要な申告", "testing": "テストの案内",
                                      "devices": "実機と確認範囲"][group] ?? "").font(.headline)
                                ForEach(fields) { field in entry(field) }
                            }
                        }
                    }
                    Text("審査連絡先・テスターの宛先・パスワードは、既存の案件専用設定を使います。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let error { Text(error).foregroundStyle(.red).accessibilityLabel(error) }
            HStack {
                if busy { ProgressView().controlSize(.small).accessibilityLabel("保存・確認中") }
                Spacer()
                Button("閉じる", role: .cancel, action: close).disabled(busy).storeGateCheckID("release-sheet-close")
                Button("保存して準備を進める") { save(answers) }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy)
                    .storeGateCheckID("intake-save")
            }
        }
        .padding(22)
        .frame(width: 660, height: 660)
    }

    private func entry(_ field: ReleaseIntake.Field) -> some View {
        let answer = Binding(get: { answers[field.key] ?? "" }, set: { answers[field.key] = $0 })
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(field.title)
                if let status = field.status { Text(status).font(.caption).foregroundStyle(.secondary) }
            }
            if field.options.isEmpty {
                HStack(alignment: .top) {
                    TextField(field.title, text: answer, prompt: Text(field.key == "test_plan" ? "端末名: 操作名" : field.hint),
                              axis: field.key == "test_plan" ? .vertical : .horizontal)
                        .labelsHidden()
                        .textFieldStyle(.roundedBorder)
                        .disabled(answer.wrappedValue == "pending")
                        .storeGateCheckID("intake-\(field.key)")
                    Toggle("未定", isOn: Binding(get: { answer.wrappedValue == "pending" },
                                                 set: { answer.wrappedValue = $0 ? "pending" : field.value == "pending" ? "" : field.value }))
                }
            } else {
                Picker(field.title, selection: answer) {
                    Text("未記入").tag("")
                    ForEach(field.options, id: \.value) { option in Text(option.label).tag(option.value) }
                }
                .labelsHidden()
                .storeGateCheckID("intake-\(field.key)")
            }
            Text(field.hint).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
