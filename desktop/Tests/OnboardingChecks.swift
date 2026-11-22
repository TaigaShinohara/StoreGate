import Foundation

@main
struct OnboardingChecks {
    @MainActor
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        var flow = OnboardingState()
        precondition(flow.pages.count == 13 && Set(flow.pages).count == 13)
        precondition(flow.pages.filter { [.register, .verify, .record].contains($0) } == [.register, .verify, .record])
        precondition(!flow.choose(.starting) && !flow.practice())
        precondition(flow.advance(from: .welcome) && flow.page == .goal)
        precondition(!flow.advance(from: .goal))
        precondition(flow.choose(.checks) && flow.page == .goal)
        precondition(flow.mediumPraiseIndex == 0)
        precondition(flow.advance(from: .goal) && flow.page == .match)
        precondition(flow.headline.contains(OnboardingFocus.checks.title))
        precondition(flow.pages[4] == .remainingChecks)
        precondition(!flow.advance(from: .goal), "A delayed task must not advance a second page")
        for page in flow.pages {
            flow.page = page
            precondition(flow.dotWindow.count <= 5 && flow.dotWindow.contains(flow.index))
        }
        precondition(flow.dotWindow.upperBound == flow.pages.count)
        flow.page = .practice
        precondition(!flow.advance(from: .practice))
        precondition(flow.practice() && flow.hasPracticed && flow.practicedThisRun)
        precondition(flow.mediumPraiseIndex == 1)
        precondition(flow.advance(from: .practice) && flow.page == .practiceResult)
        precondition(flow.back() && flow.page == .practice)
        precondition(!flow.finish(.completed))
        precondition(flow.finish(.deferred) && flow.isComplete)
        precondition(flow.restart(at: .practice) && flow.isComplete && !flow.practicedThisRun && flow.hasPracticed)
        precondition(!flow.advance(from: .practice), "A replay must wait for this run's practice")
        precondition(!flow.restart(at: .start))

        let skippedURL = root.appendingPathComponent("skipped/onboarding-state.json")
        let skipped = OnboardingStore(url: skippedURL)
        precondition(skipped.automaticallyPresents(hasProjects: false, workspaceHealthy: true))
        precondition(!skipped.automaticallyPresents(hasProjects: true, workspaceHealthy: true))
        precondition(!skipped.automaticallyPresents(hasProjects: false, workspaceHealthy: false))
        precondition(skipped.finish(.skipped))
        let skippedAgain = OnboardingStore(url: skippedURL)
        precondition(skippedAgain.state.isComplete && !skippedAgain.state.hasPracticed)
        precondition(!skippedAgain.automaticallyPresents(hasProjects: false, workspaceHealthy: true))

        let laterURL = root.appendingPathComponent("later.json")
        var laterState = OnboardingState(); laterState.page = .practice
        try JSONEncoder().encode(laterState).write(to: laterURL)
        let later = OnboardingStore(url: laterURL)
        precondition(later.finish(.deferred))
        let laterAgain = OnboardingStore(url: laterURL)
        precondition(laterAgain.state.completion == .deferred && laterAgain.state.isComplete && !laterAgain.state.hasPracticed)
        precondition(laterAgain.restart(at: .practice) && laterAgain.state.isComplete)

        let completeURL = root.appendingPathComponent("complete.json")
        let complete = OnboardingStore(url: completeURL)
        complete.visit()
        precondition(complete.advance(from: .welcome) && complete.choose(.starting))
        precondition(complete.advance(from: .goal))
        while complete.state.page != .practice {
            precondition(complete.advance(from: complete.state.page))
        }
        precondition(complete.practice())
        while complete.state.page != .start {
            precondition(complete.advance(from: complete.state.page))
        }
        precondition(complete.finish(.completed))
        let completedAgain = OnboardingStore(url: completeURL)
        precondition(completedAgain.state.focus == .starting && completedAgain.state.hasPracticed && completedAgain.state.isComplete)
        precondition(completedAgain.state.counters["view.welcome"] == 1)
        precondition(completedAgain.state.counters["finish.completed"] == 1)
        precondition(!completedAgain.state.counters.keys.contains { $0.contains("starting") || $0.contains("exploring") })
        precondition(!FileManager.default.fileExists(atPath: root.appendingPathComponent("delivery-workspace.json").path))
        let firstPraise = completedAgain.state.largePraiseIndex
        precondition(completedAgain.restart(at: .welcome) && completedAgain.state.completion == nil)
        completedAgain.leave()
        precondition(completedAgain.state.counters["leave.welcome"] == 1 && completedAgain.state.isComplete)
        var replay = completedAgain.state
        precondition(replay.restart(at: .welcome))
        replay.page = .start
        precondition(replay.finish(.completed) && replay.largePraiseIndex != firstPraise)

        let resumeURL = root.appendingPathComponent("resume.json")
        let resume = OnboardingStore(url: resumeURL)
        precondition(resume.advance(from: .welcome) && resume.choose(.checks))
        precondition(resume.advance(from: .goal) && resume.advance(from: .match))
        let resumed = OnboardingStore(url: resumeURL)
        precondition(resumed.state.page == .identity && resumed.state.focus == .checks && !resumed.state.isComplete)
        precondition(resumed.back() && resumed.state.page == .match)

        // A stale window cannot overwrite completion saved by another window.
        let sharedURL = root.appendingPathComponent("shared.json")
        let a = OnboardingStore(url: sharedURL)
        let b = OnboardingStore(url: sharedURL)
        precondition(a.advance(from: .welcome))
        precondition(!b.finish(.skipped) && b.problem != nil && !b.state.isComplete)
        b.reload()
        precondition(b.finish(.skipped))
        precondition(!a.choose(.starting) && a.problem != nil)
        precondition(OnboardingStore(url: sharedURL).state.isComplete)

        let brokenURL = root.appendingPathComponent("broken.json")
        let brokenBytes = Data("not JSON".utf8)
        try brokenBytes.write(to: brokenURL)
        let broken = OnboardingStore(url: brokenURL)
        precondition(broken.loadFailed && broken.problem != nil)
        precondition(!broken.automaticallyPresents(hasProjects: false, workspaceHealthy: true))
        precondition(!broken.finish(.skipped) && !broken.state.isComplete)
        let preservedBroken = try Data(contentsOf: brokenURL)
        precondition(preservedBroken == brokenBytes)

        let blocked = root.appendingPathComponent("not-a-directory")
        try Data("keep".utf8).write(to: blocked)
        let cannotSave = OnboardingStore(url: blocked.appendingPathComponent("state.json"))
        precondition(!cannotSave.finish(.skipped) && !cannotSave.state.isComplete)
        precondition(cannotSave.problem != nil)
        let preservedBlocked = try Data(contentsOf: blocked)
        precondition(preservedBlocked == Data("keep".utf8))

        let upgradedURL = root.appendingPathComponent("upgrade.json")
        var old = OnboardingState(); old.version = 0; old.page = .start
        try JSONEncoder().encode(old).write(to: upgradedURL)
        let upgraded = OnboardingStore(url: upgradedURL)
        precondition(upgraded.state.version == OnboardingState.currentVersion && upgraded.state.page == .welcome)
        precondition(upgraded.automaticallyPresents(hasProjects: false, workspaceHealthy: true))
        precondition(upgraded.finish(.skipped))
        var future = OnboardingState(); future.version = OnboardingState.currentVersion + 1
        let futureBytes = try JSONEncoder().encode(future)
        try futureBytes.write(to: upgradedURL)
        let unsupported = OnboardingStore(url: upgradedURL)
        precondition(unsupported.loadFailed && !unsupported.finish(.skipped))
        let preservedFuture = try Data(contentsOf: upgradedURL)
        precondition(preservedFuture == futureBytes)

        var counters = OnboardingState(); counters.counters["test"] = Int.max
        counters.count("test")
        precondition(counters.counters["test"] == 1_000_000)
        print("Onboarding: choices, three steps, practice/defer/skip, resume, version gates, conflicts and failed storage passed.")
    }
}
