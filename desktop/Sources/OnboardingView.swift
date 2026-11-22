import AppKit
import SwiftUI

enum OnboardingDestination: Equatable { case projects, createProject }

@MainActor
struct OnboardingView: View {
    @ObservedObject var store: OnboardingStore
    let finish: (OnboardingDestination) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AccessibilityFocusState private var headingFocused: Bool
    @FocusState private var focusedAction: Action?
    @State private var advanceTask: Task<Void, Never>?
    @State private var reacting = false
    @State private var leaving = false
    @State private var transitioning = false
    @State private var celebrating = false
    @State private var closed = false
    @State private var headlineVisible = false
    @State private var artworkVisible = false
    @State private var detailVisible = false
    @State private var smallAcknowledged = false
    @State private var response: String?
    @State private var pendingDestination: OnboardingDestination?

    private enum Action: Hashable { case primary, choice(OnboardingFocus), practice, back, skip }
    private var page: OnboardingPage { store.state.page }
    private var motion: Animation? { reduceMotion ? .easeOut(duration: 0.08) : .easeInOut(duration: 0.2) }

    var body: some View {
        VStack(spacing: 16) {
            header
            ScrollView {
                VStack(spacing: 22) {
                    if page == .goal {
                        headline
                        choices
                            .opacity(artworkVisible ? 1 : 0)
                            .accessibilityHidden(!artworkVisible)
                    } else {
                        artwork
                            .frame(height: 200)
                            .opacity(artworkVisible ? 1 : 0)
                            .accessibilityHidden(!artworkVisible)
                        headline
                        if !store.state.detail.isEmpty {
                            Text(store.state.detail)
                                .font(.title3)
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true)
                                .opacity(detailVisible ? 1 : 0)
                                .accessibilityHidden(!detailVisible)
                                .accessibilitySortPriority(2)
                        }
                    }
                    if let response {
                        Label(response, systemImage: "checkmark.circle.fill")
                            .font(.callout.weight(.medium))
                            .multilineTextAlignment(.center)
                            .transition(.opacity)
                    }
                }
                .frame(maxWidth: 620)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
                .opacity(leaving ? 0 : 1)
                .accessibilityHidden(leaving)
            }
            .frame(maxHeight: .infinity)
            if let problem = store.problem {
                VStack(alignment: .leading, spacing: 8) {
                    WorkNotice(text: problem)
                    Button("進み具合を読み直す") { cancelAdvance(); store.reload() }
                }
            }
            footer.frame(minHeight: 48)
        }
        .padding(.horizontal, 36)
        .padding(.vertical, 22)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .task(id: page) {
            store.visit()
            reacting = false; transitioning = false; leaving = false
            smallAcknowledged = false; response = nil
            headlineVisible = false; artworkVisible = false; detailVisible = false
            withAnimation(motion) { headlineVisible = true }
            headingFocused = true
            do {
                try await Task.sleep(for: .milliseconds(140))
                withAnimation(motion) { artworkVisible = true }
                try await Task.sleep(for: .milliseconds(140))
                withAnimation(motion) { detailVisible = true }
                if focusedAction == nil && !reacting {
                    focusedAction = page == .goal ? .choice(.starting) : page == .practice ? .practice : .primary
                }
            } catch { }
        }
        .onKeyPress(keys: [.return, .rightArrow]) { _ in
            if let pendingDestination { close(pendingDestination); return .handled }
            if reacting { advance(immediately: true); return .handled }
            return .ignored
        }
        .onDisappear {
            advanceTask?.cancel()
            store.leave()
        }
    }

    private var header: some View {
        HStack {
            Button("戻る", systemImage: "chevron.left") {
                guard !closed else { return }
                cancelAdvance()
                _ = store.back()
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.primary)
            .keyboardShortcut(.leftArrow, modifiers: [])
            .focused($focusedAction, equals: .back)
            .disabled(store.state.index == 0 || store.problem != nil)
            Spacer()
            windowDots
            Spacer()
            Button(store.problem == nil ? "スキップ" : "保存せず閉じる") {
                guard !closed else { return }
                cancelAdvance()
                if store.problem != nil || store.finish(.skipped) { close(.projects) }
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.primary)
            .keyboardShortcut(.cancelAction)
            .focused($focusedAction, equals: .skip)
        }
        .accessibilitySortPriority(-1)
    }

    private var windowDots: some View {
        let window = store.state.dotWindow
        return HStack(spacing: 0) {
            ForEach(Array(store.state.pages.enumerated()), id: \.element) { index, _ in
                let current = index == store.state.index
                let edge = index == window.lowerBound && window.lowerBound > 0
                    || index == window.upperBound - 1 && window.upperBound < store.state.pages.count
                Capsule()
                    .fill(current ? Color.primary : Color.secondary.opacity(0.35))
                    .frame(width: current ? 18 : edge ? 4 : 7, height: current ? 7 : edge ? 4 : 7)
                    .frame(width: 22, height: 14)
            }
        }
        .offset(x: -CGFloat(window.lowerBound) * 22)
        .frame(width: CGFloat(window.count) * 22, height: 14, alignment: .leading)
        .clipped()
        .animation(motion, value: store.state.index)
        .accessibilityHidden(true)
    }

    private var headline: some View {
        Text(store.state.headline)
            .font(.largeTitle.weight(.semibold))
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .opacity(headlineVisible ? 1 : 0)
            .accessibilityAddTraits(.isHeader)
            .accessibilityLabel(page.chapter + "。" + store.state.headline)
            .accessibilityFocused($headingFocused)
            .accessibilitySortPriority(3)
    }

    private var choices: some View {
        VStack(spacing: 12) {
            ForEach(OnboardingFocus.allCases) { focus in
                Button { choose(focus) } label: {
                    HStack(spacing: 16) {
                        Image(systemName: focus.symbol).font(.title2).frame(width: 32)
                            .accessibilityHidden(true)
                        Text(focus.title).font(.headline)
                        Spacer()
                        if store.state.focus == focus {
                            Image(systemName: "checkmark.circle.fill").accessibilityHidden(true)
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, minHeight: 60)
                    .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(
                        store.state.focus == focus || focusedAction == .choice(focus) ? Color.primary : Color.secondary.opacity(0.25),
                        lineWidth: focusedAction == .choice(focus) ? 2 : 1))
                    .scaleEffect(!reduceMotion && reacting && store.state.focus == focus ? 1.015 : 1)
                }
                .buttonStyle(.plain)
                .focused($focusedAction, equals: .choice(focus))
                .accessibilityValue(store.state.focus == focus ? "選択済み" : "")
                .accessibilityHint("選ぶと、反応の後に次の案内へ進みます。")
                .disabled(store.problem != nil)
            }
        }
        .animation(motion, value: reacting)
    }

    @ViewBuilder private var artwork: some View {
        if page == .practice {
            Button { practice() } label: {
                OnboardingArtwork(page: .nextTask, practiced: reacting)
                    .scaleEffect(!reduceMotion && reacting ? 1.025 : 1)
            }
            .buttonStyle(.plain)
            .focused($focusedAction, equals: .practice)
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary, lineWidth: focusedAction == .practice ? 2 : 0))
            .accessibilityLabel("次の作業「配信の設定」を開く")
            .accessibilityHint("押すと、反応の後に作業画面の見本が開きます。")
            .disabled(store.problem != nil)
            .animation(motion, value: reacting)
        } else {
            OnboardingArtwork(page: page, practiced: celebrating)
                .scaleEffect(!reduceMotion && celebrating ? 1.03 : 1)
                .accessibilitySortPriority(1)
                .animation(motion, value: celebrating)
        }
    }

    @ViewBuilder private var footer: some View {
        if page == .practice {
            Button("あとで試す") {
                guard !closed else { return }
                cancelAdvance()
                if store.finish(.deferred) { close(.projects) }
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.primary)
            .disabled(store.problem != nil)
        } else if page != .goal {
            Button {
                if let pendingDestination { close(pendingDestination) }
                else if page == .start { complete() }
                else { advance() }
            } label: {
                Label(page == .start ? "アプリを登録する" : "次へ",
                      systemImage: smallAcknowledged || celebrating ? "checkmark" : "arrow.right")
                    .padding(.horizontal, 16)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .focused($focusedAction, equals: .primary)
            .disabled(store.problem != nil)
            .scaleEffect(reduceMotion ? 1 : celebrating ? 1.025 : smallAcknowledged ? 0.98 : 1)
            .animation(motion, value: smallAcknowledged)
            .animation(motion, value: celebrating)
        }
    }

    private func choose(_ focus: OnboardingFocus) {
        guard !closed, page == .goal else { return }
        if reacting && store.state.focus == focus { advance(immediately: true); return }
        cancelAdvance()
        guard store.choose(focus) else { return }
        let messages: [String]
        switch focus {
        case .starting: messages = ["始め方から見ていきましょう。", "最初の一歩を、一緒に確かめましょう。", "進め方を知る準備ができました。"]
        case .checks: messages = ["確認したいことを受け取りました。", "確認の進め方から見ていきましょう。", "残る確認を見つける準備ができました。"]
        case .exploring: messages = ["まずは、流れを見てみましょう。", "気になるところから試せます。", "見てから決めて大丈夫です。"]
        }
        react(messages)
    }

    private func practice() {
        guard !closed, page == .practice else { return }
        if reacting { advance(immediately: true); return }
        guard store.practice() else { return }
        react(["次の作業を開けました。", "作業の入口を見つけました。", "最初の一歩を試せました。"])
    }

    private func react(_ messages: [String]) {
        withAnimation(motion) {
            response = messages[store.state.mediumPraiseIndex]
            reacting = true
        }
        NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now)
        let source = page
        advanceTask = Task { @MainActor in
            do { try await Task.sleep(for: .milliseconds(950)) } catch { return }
            guard !Task.isCancelled, store.state.page == source else { return }
            advance(feedback: false)
        }
    }

    private func advance(immediately: Bool = false, feedback: Bool = true) {
        guard !closed, !transitioning, page != .start else { return }
        advanceTask?.cancel()
        reacting = false; transitioning = true; smallAcknowledged = feedback
        if feedback { NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now) }
        let source = page
        withAnimation(motion) { leaving = true }
        advanceTask = Task { @MainActor in
            if !immediately {
                do { try await Task.sleep(for: .milliseconds(180)) } catch { return }
            }
            guard !Task.isCancelled, !closed, store.state.page == source else { return }
            if !store.advance(from: source) { leaving = false; transitioning = false }
        }
    }

    private func complete() {
        guard !closed, pendingDestination == nil, store.finish(.completed) else { return }
        let messages = ["最初の一歩を試せました。", "使い始める準備が整いました。", "案内を最後まで確認できました。"]
        response = messages[store.state.largePraiseIndex]
        pendingDestination = .createProject
        celebrating = true
        NSHapticFeedbackManager.defaultPerformer.perform(.generic, performanceTime: .now)
        advanceTask = Task { @MainActor in
            do { try await Task.sleep(for: .milliseconds(650)) } catch { return }
            guard !Task.isCancelled, !closed else { return }
            close(.createProject)
        }
    }

    private func cancelAdvance() {
        advanceTask?.cancel()
        advanceTask = nil
        reacting = false; transitioning = false; leaving = false
        pendingDestination = nil; celebrating = false
    }

    private func close(_ destination: OnboardingDestination) {
        guard !closed else { return }
        closed = true
        advanceTask?.cancel()
        finish(destination)
    }
}

private struct OnboardingArtwork: View {
    let page: OnboardingPage
    var practiced = false

    var body: some View {
        Group {
            switch page {
            case .welcome:
                HStack(spacing: 20) {
                    symbol("app", label: "アプリ")
                    Image(systemName: "arrow.right").foregroundStyle(.secondary)
                    symbol("checklist", label: "準備")
                    Image(systemName: "arrow.right").foregroundStyle(.secondary)
                    symbol("storefront", label: "配信")
                }
            case .goal, .match:
                Image(systemName: "arrow.triangle.branch")
                    .font(.system(size: 100, weight: .light))
            case .identity, .start:
                mockCard {
                    Label(page == .start ? "あなたのアプリ" : "配信案件", systemImage: "app.dashed")
                        .font(.title2.weight(.medium))
                    Text("Mac App Store").foregroundStyle(.secondary)
                    if page == .start { Image(systemName: practiced ? "checkmark.circle.fill" : "plus.circle").font(.largeTitle) }
                }
            case .nextTask, .practice:
                mockCard {
                    Text("配信の工程").font(.callout).foregroundStyle(.secondary)
                    HStack {
                        Label(DeliveryStep.scope.title, systemImage: "arrow.right.circle.fill")
                            .font(.title2.weight(.medium))
                        Spacer()
                        Image(systemName: "arrow.right")
                    }
                }
            case .remainingChecks, .verify:
                mockCard {
                    if page == .verify { stepNumber(2) }
                    Label("アカウント", systemImage: "checkmark.circle.fill")
                    Label("掲載情報", systemImage: "circle")
                    Label("配布用ビルド", systemImage: "circle")
                }
            case .register, .practiceResult:
                mockCard {
                    if page == .register { stepNumber(1) }
                    else { Label(DeliveryStep.scope.title, systemImage: "slider.horizontal.3").font(.headline) }
                    Label(page == .register ? "アプリ名" : "バージョン", systemImage: "pencil")
                    RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.12)).frame(height: 24)
                    Text(page == .register ? "プロジェクトのフォルダ · Bundle ID" : "配信する版の入力欄")
                        .font(.callout).foregroundStyle(.secondary)
                }
            case .record:
                mockCard {
                    stepNumber(3)
                    Label("App Store Connect", systemImage: "arrow.up.right.square")
                    Image(systemName: "arrow.down").foregroundStyle(.secondary)
                    Label("確認した状態を記録", systemImage: "checkmark.bubble")
                }
            case .expectation:
                mockCard {
                    Label("確認結果を保存", systemImage: "checkmark.circle.fill")
                    Divider()
                    Label("次の作業から再開", systemImage: "arrow.clockwise")
                }
            }
        }
        .frame(maxWidth: 440, maxHeight: .infinity)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(description)
    }

    private func symbol(_ name: String, label: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: name).font(.system(size: 48, weight: .light))
            Text(label).font(.callout)
        }
    }
    private func stepNumber(_ number: Int) -> some View {
        Image(systemName: "\(number).circle.fill").font(.largeTitle).accessibilityHidden(true)
    }
    private func mockCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 14, content: content)
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.secondary.opacity(0.22)))
    }
    private var description: String {
        switch page {
        case .welcome: "アプリから準備を経て、配信へ進む道順。"
        case .goal, .match: "目的に合わせて進む道順。"
        case .identity, .start: "アプリごとに作業をまとめる配信案件の見本。"
        case .nextTask, .practice: "次の作業として配信の設定を開くカード。"
        case .remainingChecks, .verify: "確認済みと未確認を分けた一覧の見本。"
        case .register: "アプリ名とプロジェクトを登録する画面の見本。"
        case .practiceResult: "配信設定の入力欄が開いた見本。実案件はこのあと登録します。"
        case .record: "公式画面で見た状態を、StoreGateに記録する流れ。"
        case .expectation: "保存した結果から次の作業を再開する見本。"
        }
    }
}
