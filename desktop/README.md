# StoreGate Desktop（試作）

日本向けのサンプルMacアプリで、配信案件の確認からストア公開の確認までを体験するmacOSアプリです。各画面に次の作業・完了の目安・元のStoreGateの資料を表示します。進捗はMac内に保存され、再起動しても続きから始められます。

MacとSwift 6を含むXcodeのコマンドラインツールが必要です。リポジトリのルートから実行します。

```bash
bash desktop/build.sh
open desktop/.build/StoreGate.app
bash desktop/build.sh --test
```

サンプル案件は `com.example.storegate.sample` のバージョン 1.0、ビルド 1 から始まります。Xcodeの `.xcarchive` を選ぶと、アーカイブに記載されたBundle ID、バージョン、ビルド番号を照合します。「サンプルで照合」でも次のステップを試せます。「差し戻しを試す」からは、掲載情報だけの修正とビルドの差し替えを選べます。ビルドを差し替えると、次のビルド番号で再照合します。

審査提出や公開は画面上で模擬します。署名、Sandbox、バイナリ、実機動作の検証、CLI監査の実行、実際のストアへの送信は次の段階です。既存のガードはiOS、Android、Web向けで、Mac用のアーカイブ検証とは別に扱います。
