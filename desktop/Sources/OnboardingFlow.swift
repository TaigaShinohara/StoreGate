import Foundation

enum OnboardingFocus: String, Codable, CaseIterable, Identifiable {
    case starting, checks, exploring
    var id: Self { self }
    var title: String {
        switch self {
        case .starting: "何から始めるか迷っている"
        case .checks: "確認漏れを減らしたい"
        case .exploring: "まだわからない"
        }
    }
    var symbol: String {
        switch self {
        case .starting: "arrow.turn.down.right"
        case .checks: "checklist"
        case .exploring: "questionmark.circle"
        }
    }
}

enum OnboardingPage: String, Codable, CaseIterable, Identifiable {
    case welcome, goal, match, identity, nextTask, remainingChecks
    case register, verify, record, practice, practiceResult, expectation, start
    var id: Self { self }

    var title: String {
        switch self {
        case .welcome: "配信までの道順を、ひとつずつ"
        case .goal: "いま、気になるのは？"
        case .match: "あなたに合う、準備の入口から"
        case .identity: "Macの配信準備を、この場所に"
        case .nextTask: "次にすることが、見つかる"
        case .remainingChecks: "残る確認を、見渡せる"
        case .register: "登録する"
        case .verify: "確かめる"
        case .record: "記録する"
        case .practice: "次の作業を、開いてみる"
        case .practiceResult: "最初の作業が見つかりました"
        case .expectation: "続きは、次の作業から"
        case .start: "あなたのアプリで始めましょう"
        }
    }

    var subtitle: String {
        switch self {
        case .welcome: "Mac App Storeへの準備を、次にやることから。"
        case .identity: "作業と確認結果を、アプリごとに残せます。"
        case .nextTask: "案件を開くと、次の作業から再開できます。"
        case .remainingChecks: "確認していない項目を、一覧で見返せます。"
        case .register: "アプリ名・プロジェクトのフォルダ・Bundle IDをそろえます。"
        case .verify: "監査と実機の確認結果を、一件ずつ残します。"
        case .record: "App Store Connectで行った提出や公開を、ここに記録します。"
        case .practiceResult: "この画面から、配信する版を設定していきます。"
        case .expectation: "確認した結果は、このMacに残ります。"
        case .start: "登録すると、最初の作業が開きます。"
        case .goal, .match, .practice: ""
        }
    }

    var chapter: String {
        switch self {
        case .register: "使い方、登録する"
        case .verify: "使い方、確かめる"
        case .record: "使い方、記録する"
        case .practice, .practiceResult: "使い方を試す"
        default: "はじめての使い方"
        }
    }
}

enum OnboardingCompletion: String, Codable { case completed, skipped, deferred }

struct OnboardingState: Codable, Equatable {
    static let currentVersion = 1
    var version = Self.currentVersion
    var completedVersion = 0
    var page: OnboardingPage = .welcome
    var focus: OnboardingFocus?
    var practicedThisRun = false
    var hasPracticed = false
    var completion: OnboardingCompletion?
    var mediumPraiseIndex = 2
    var largePraiseIndex = 2
    // Local aggregate counts only: no session IDs, timestamps or answer values.
    var counters: [String: Int] = [:]

    var isComplete: Bool { completedVersion >= Self.currentVersion }
    var pages: [OnboardingPage] {
        let values: [OnboardingPage] = focus == .checks ? [.remainingChecks, .nextTask] : [.nextTask, .remainingChecks]
        return [.welcome, .goal, .match, .identity] + values
            + [.register, .verify, .record, .practice, .practiceResult, .expectation, .start]
    }
    var index: Int { pages.firstIndex(of: page) ?? 0 }
    var dotWindow: Range<Int> {
        let count = min(5, pages.count)
        let start = min(max(index - 2, 0), pages.count - count)
        return start..<(start + count)
    }
    var headline: String {
        guard page == .match else { return page.title }
        if let focus, focus != .exploring { return "「\(focus.title)」" }
        return "まだ決めなくて大丈夫"
    }
    var detail: String {
        if page == .match {
            switch focus {
            case .starting: return "次の作業を案内するStoreGateが、準備の入口にぴったりです。"
            case .checks: return "未確認の項目を見渡せるStoreGateが、その準備にぴったりです。"
            case .exploring, nil: return "まずは、StoreGateの進め方を試してみましょう。"
            }
        }
        if page == .start, focus == .checks { return "確認したいアプリを登録して、準備する項目を見てみましょう。" }
        return page.subtitle
    }

    mutating func count(_ key: String) {
        counters[key] = min(999_999, max(0, counters[key] ?? 0)) + 1
    }
    mutating func choose(_ value: OnboardingFocus) -> Bool {
        guard page == .goal else { return false }
        focus = value
        mediumPraiseIndex = (max(0, mediumPraiseIndex) % 3 + 1) % 3
        count("answer.goal")
        return true
    }
    mutating func practice() -> Bool {
        guard page == .practice else { return false }
        practicedThisRun = true
        hasPracticed = true
        mediumPraiseIndex = (max(0, mediumPraiseIndex) % 3 + 1) % 3
        count("practice.open")
        return true
    }
    mutating func advance(from expected: OnboardingPage) -> Bool {
        guard page == expected, index + 1 < pages.count,
              page != .goal || focus != nil, page != .practice || practicedThisRun else { return false }
        page = pages[index + 1]
        return true
    }
    mutating func back() -> Bool {
        guard index > 0 else { return false }
        page = pages[index - 1]
        return true
    }
    mutating func finish(_ reason: OnboardingCompletion) -> Bool {
        guard reason != .completed || page == .start,
              reason != .deferred || page == .practice else { return false }
        completedVersion = Self.currentVersion
        completion = reason
        if reason == .completed { largePraiseIndex = (max(0, largePraiseIndex) % 3 + 1) % 3 }
        count("finish.\(reason.rawValue)")
        return true
    }
    mutating func restart(at entry: OnboardingPage) -> Bool {
        guard entry == .welcome || entry == .practice else { return false }
        page = entry
        practicedThisRun = false
        completion = nil
        // A replay must not make a previously completed guide mandatory again.
        count("replay.\(entry.rawValue)")
        return true
    }
}
