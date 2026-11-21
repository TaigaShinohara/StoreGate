import Foundation

enum ProjectLocation {
    static func url(_ text: String, directory: Bool = true) throws -> URL {
        var path = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if path.hasPrefix("\""), path.hasSuffix("\"") { path = String(path.dropFirst().dropLast()) }
        let url: URL
        if path.hasPrefix("file:") {
            guard let file = URL(string: path), file.isFileURL, file.user == nil, file.password == nil,
                  file.host == nil || file.host == "" || file.host == "localhost" else { throw InputError.invalidPath }
            url = file
        } else {
            path = (path as NSString).expandingTildeInPath
            guard path.hasPrefix("/") else { throw InputError.invalidPath }
            url = URL(fileURLWithPath: path)
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory),
              !directory || isDirectory.boolValue else { throw InputError.missingPath }
        return url.resolvingSymlinksInPath().standardizedFileURL
    }

    enum InputError: LocalizedError {
        case invalidPath, missingPath
        var errorDescription: String? {
            switch self {
            case .invalidPath: "Finderで項目を選び、パスをコピーして貼り付けるか、この画面にドラッグしてください。"
            case .missingPath: "指定した項目が見つからないか、必要なフォルダではありません。接続とパスを確認してください。"
            }
        }
    }
}

enum StoreLocales {
    // Apple App Store Connect's metadata locales, verified 2026-11-21.
    // https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations
    static let codes = ["ar-SA", "bn-BD", "ca", "cs", "da", "de-DE", "el", "en-AU", "en-CA", "en-GB", "en-US",
                        "es-ES", "es-MX", "fi", "fr-FR", "fr-CA", "gu-IN", "he", "hi", "hr", "hu", "id", "it",
                        "ja", "kn-IN", "ko", "ml-IN", "mr-IN", "ms", "nl-NL", "no", "or-IN", "pa-IN", "pl", "pt-BR", "pt-PT",
                        "ro", "ru", "sk", "sl-SI", "sv", "ta-IN", "te-IN", "th", "tr", "uk", "ur-PK", "vi", "zh-Hans", "zh-Hant"]
    static func canonical(_ value: String) -> String? {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "_", with: "-")
        return codes.first { $0.caseInsensitiveCompare(normalized) == .orderedSame }
    }
    static func label(_ code: String) -> String {
        let name = Locale(identifier: "ja").localizedString(forIdentifier: code) ?? code
        return "\(name)（\(code)）"
    }
}
