# StoreGate Desktop

Mac App Storeへの配信を、作業・確認結果・次の一手ごとに案内するmacOSアプリです。案件一覧から実案件を登録するか、別枠のサンプルで流れを試せます。

macOS 14以降と、Swift 6・Python 3が使えるXcodeコマンドラインツールが必要です。リポジトリのルートから実行します。

```bash
bash desktop/build.sh
open desktop/.build/StoreGate.app
bash desktop/build.sh --test
```

1. 「新しい配信案件」でアプリ名・プロジェクトフォルダ・Bundle IDを登録します。フォルダはパスの貼り付け、Finderからのドラッグ、選択ダイアログで指定できます。存在しない場所は保存できません。「案件の基本情報を編集…」から名前・場所・Bundle IDを訂正できます。場所や識別情報の変更時は関連する確認をやり直します。続けて版、ビルド番号、地域、機能、公開方法を保存します。
2. 開発者アカウントの確認や待機理由を記録します。待機中に原稿、画像、審査メモなどを用意できます。
3. 「適用ルールと実機」でMacソース監査を実行します。macOSのSDK設定、権限の用途説明、HTTP接続先、ポリシー参照、Privacy Manifestを静的に検査します。ビルドスクリプトは実行しません。監査範囲外の署名資格・実機・申告は手動確認です。掲載情報を入力し、既存のStoreGate監査で確認します。指摘の項目へ戻って修正し、言語ごとに再検査します。掲載言語はApple対応一覧から選択し、追加した言語は原稿を確認して削除できます。主要掲載言語は配信設定で変更します。指摘の理由と直し方は日本語で表示し、詳細から原文を読めます。監査は入力した文面とURLの形式を検証します。ストアに実際に保存した文面・素材・申告、URLの到達性は別途照合します。
4. Xcodeの `.xcarchive` を、パスの貼り付け・ドラッグ・選択ダイアログで指定し、識別情報・署名の静的整合性・署名されたSandbox設定を照合します。配布用署名の資格、権限、実機動作、対象地域に適用される規制は、画面の案内に沿って確認します。
5. 公式画面で対象ビルドをアップロードし、処理完了、審査への提出、審査結果、配信地域ごとの公開を記録します。差し戻しの際は理由と影響範囲を選び、該当する工程を開き直します。

確認済みの工程は一覧から開いて根拠を見返せます。案件は `~/Library/Application Support/StoreGate/delivery-workspace.json` に保存されます。読込に失敗した場合、再読込や、元ファイルを別名に保管してから直前のバックアップを復元できます。サンプルの保存先は別です。

保存が競合した場合は「保存を再試行」で別案件への変更を反映できます。同じ案件で競合した場合は「変更を控えに保存して読み直す」で未保存の内容を別のJSONファイルに残し、最新の案件を開けます。アップロード後に別のアーカイブを使う場合は、Xcodeでビルド番号を更新してから再照合します。

従来のMac案件画面では、App Store Connectで行ったアップロード・審査・公開の結果を本人が記録します。ローカルの検査結果と、本人による申告・実機・ストア照合を画面で区別します。

提出前には、ソース監査とアーカイブの保存済み結果が変わっていないことを再照合します。ソースの変更、基本情報の訂正、監査エンジンの更新に応じて再確認が必要です。古い保存データの掲載監査には一度再検査が必要です。審査中・公開済みの当時の確認履歴は、翌日や監査エンジン更新後も完了記録として保持します。

Macソース監査は `python3 scripts/macos-source-audit.py "/path/to/mac-app"` でも実行できます。既存ガードもmacOS専用の設定を検出するとこの監査へ振り分け、iOSとは別に報告します。複数プラットフォームの混在や容量上限、読取失敗は検査完了にしません。`.local`、`.build`、ビルド成果物、依存ライブラリとテストは対象外です。

対応する掲載言語とコードは、[Appleの対応言語一覧](https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations)と[公式ロケールコード表](https://developer.apple.com/documentation/appstoreconnectapi/managing-metadata-in-your-app-by-using-locale-shortcodes)を2026-11-21に確認しました。日本語の監査案内の正本は `desktop/Resources/Audit.xcstrings` です。

「適用ルールと実機」はソース監査・適用ルール・手動確認、「掲載情報と審査メモ」は掲載原稿・審査メモ・ストアとの照合を切り替えて使います。補助情報と保存済みの記録は見出しから開けます。[画面の情報整理と検証記録](INFORMATION-DESIGN.md)に比較結果と未確認の範囲を記載しています。

案件のない初回起動では、希望の選択、3ステップの使い方、試し操作を順に案内します。スキップや「あとで試す」で通常画面へ進めます。案件一覧の「使い方」から全体の再表示と試し操作を選べます。進み具合は案件とは別の `onboarding-state.json` に保存します。[オンボーディングのページ地図と検証記録](ONBOARDING.md)を参照してください。

## 複数アプリの配信案件

起動後の「配信案件」から、既存アプリと対象版の設定を登録できます。GUIとCLIは同じSQLiteを使います。保存先は既定で `~/Library/Application Support/StoreGate/delivery.sqlite3` です。検証時は `STOREGATE_WORKSPACE_DIR` で別フォルダを指定できます。従来のMac案件は初回表示で既存JSONの履歴を取り込み、元の案件画面は「Mac App Storeの従来の案件」から開けます。

[プロファイルの例](../templates/delivery-profile.example.json)と[版の設定例](../templates/delivery-release.example.json)をローカルにコピーし、アプリに合わせて編集してください。設定内のパスは設定ファイルからの相対パスまたは絶対パスです。ビルドコマンド内のプロジェクト相対パスはリポジトリが基準です。ビルド、QA、再撮影のコマンドには、実行内容を確認したアプリの手順を登録してください。トークンや鍵は設定に入れず、`auth_env` に認証ランナーが発行する環境変数の名前を設定します。

```bash
python3 delivery/storegate.py register /path/to/profile.json
python3 delivery/storegate.py release /path/to/release.json
python3 delivery/storegate.py prepare example/ios/1.0.0/1/production
python3 delivery/storegate.py status example/ios/1.0.0/1/production
```

- `prepare` はポリシーガードと原稿の監査、素材・成果物の照合を実行します。IDの省略時は登録済みの各版を監査します。原稿は既存のmetadata-auditと同じディレクトリ形式です。撮影済みパックの画像は `07_submission/image-manifest.json` を使い、端末枠・言語・順序と実寸・形式・ハッシュを照合します。
- `build` はFlutterの登録済みコマンド、Xcodeのアーカイブと書き出し、またはGradleのRelease AABを実行します。Xcodeには `project`、`scheme`、`archive`、`export_options`、`export_path` が必要です。Androidには `bundletool` とアップロード証明書の `signer_sha256`、Appleには `team_id` が必要です。既存成果物は版の設定の `artifact` で登録できます。
- `draft` はAppleの編集可能な版に画像と原稿を配置し、読み戻します。プロファイルの `placement_groups` に、画像の端末枠とAppleの現行Asset Libraryの配置グループの対応を指定します。Appleのアプリ登録と版・掲載言語の作成はApp Store Connectで行います。
- Appleの `upload-build` は書き出したIPA・PKGをTransporterで投入し、処理後の再実行で対象ビルドの紐づけと読み戻しを行います。プロファイルにチームの `issuer_id`、`transporter_key_id`、プロバイダの短縮名を設定し、既存の認証ランナーから利用します。Google Playは対象トラックのversionCodeを照会します。テストトラックに限り、書き込み権限を確認してプロファイルで `play_staging` を有効にすると、編集中のAAB・画像・掲載情報を検証できます。編集を確定する場合は、Play Consoleで審査・公開への影響を確認してください。
- `submit` と `publish` は操作の確認内容をJSONで返します。Appleで実行する場合は最新の内容を確認し、`--approve` に返された確認値を渡します。確認値は対象版の成果物・掲載内容・配信条件・ストアの状態に紐づきます。Google PlayはPublishing overviewで審査と公開を操作し、`store-status` で照会します。公開後は `attest <ID> publication --region JP --note <確認根拠>` で予定地域の入手確認を記録できます。
