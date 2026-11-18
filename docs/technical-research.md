# Lupio MVP 技術調査

調査日: 2026-11-19。公開一次資料の机上調査。実アカウント・対象 OS での試作検証は未実施。

## 結論と推奨案

ローカルに追跡対象を保持し、GitHub の現在状態と新しいイベントを照合する方式が適している。技術構成の第一候補は Tauri 2 + TypeScript の UI + Rust の処理層 + SQLite。これは設計上の推奨であり、性能達成を示す実測結果ではない。

## デスクトップ基盤

| 候補 | 利点 | 確認すべき点 |
| --- | --- | --- |
| Tauri 2 | OS の WebView と Rust の処理層でローカル UI と API 処理を分離できる | Linux の WebKitGTK 等の依存、OS 間の表示差、Rust の開発環境 |
| Electron | Chromium を含み表示環境をそろえやすい。JavaScript/TypeScript 中心で構築できる | 配布サイズと常駐資源を実測する必要がある |

軽い一覧 UI が中心で外部サイトを埋め込まないため Tauri を第一候補にする。フレームワーク選択だけではフリーズやメモリ増加を防げず、長時間計測が必要。

一次資料: [Tauri architecture](https://v2.tauri.app/concept/architecture/)、[必要な開発環境](https://v2.tauri.app/start/prerequisites/)、[Electron security](https://www.electronjs.org/docs/latest/tutorial/security)。

## GitHub 接続と権限

ブラウザ認証の OAuth authorization code + PKCE を検討する。ただし公開資料ではコード交換に client_secret が必要で、配布バイナリに埋めても秘密にはできない。秘密を保管する交換用サービスを置く方式と、サーバー不要の device flow を比較する。device flow は secret 不要だが、GitHub は主に CLI 等の用途を示している。サーバーの追加は合意済みの構成ではないため勝手に導入しない。

OAuth の private repository 用 repo スコープは書き込み権限も含む。アプリが読むだけであっても、トークンまで読み取り専用とは言えない。GitHub App は細かい権限とリポジトリ選択ができるが、組織へのインストールやチーム情報へのアクセスを検証する必要がある。方式は未確定。実装の最初の調査タスクで、対象組織の承認・SSO と必要 API のアクセスを確認する。

認証情報は macOS Keychain、Linux の利用可能な資格情報ストアに格納する。Rust の keyring 系は候補だが、現在の公式説明ではストアを明示的に選ぶアプリ向けに keyring-core 等の利用を案内している。ライブラリと Linux ストアを試作で確定する。ストアが利用不能なら平文保存へ黙ってフォールバックしない。

一次資料: [OAuth 認証](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)、[OAuth 推奨事項](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/best-practices-for-creating-an-oauth-app)、[スコープ](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)、[GitHub App との比較](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)、[keyring](https://docs.rs/keyring/latest/keyring/)。

## 依頼の収集と追跡

検索で個人・選択チームの未終了 PR を発見し、requested_reviewers の users と teams で依頼元を確認する。検索候補は個人用 user-review-requested:@me、チーム用 team-review-requested:ORG/TEAM と is:pr is:open の組み合わせ。利用トークンで各クエリを試作確認する。

レビュー提出後に依頼一覧から消えるため、検索結果だけを毎回一覧にしてはならない。取得した PR の安定 ID、リポジトリ ID と番号を保存し、依頼解除後も個別に取得する。検索の上限・ページング・未完了結果を処理し、取得不足を空の成功結果と区別する。

一次資料: [検索条件](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)、[review requests](https://docs.github.com/en/rest/pulls/review-requests)、[検索 API](https://docs.github.com/en/rest/search/search)。

## 更新検知と API 予算

PR の状態・ドラフト・head SHA、レビュー依頼イベント、本文コメント、インラインコメント、提出レビューを照合する。updated_at は再取得のヒントであり、対応が必要な理由そのものには使わない。コメントの ID・作成時刻・投稿者 ID・Bot 種別を保持する。メンションは単なる部分文字列一致ではなく GitHub の表記に沿って判定し、引用やコード内の扱いをテストする。

手動で対応済みにした時点までの取得済みイベントを保存し、同じコメントを繰り返し通知しない。初回取得の古いイベントも新着扱いしない。取得の一部だけが成功したときは未取得部分の既読境界を進めない。PR の終了、個人再依頼、対象外指定、ドラフト、通常の更新を明示した優先順位で処理する。

約一分の更新は目標とし、同時更新をまとめ、条件付きリクエスト・ページング・有限の並列数を利用する。30 PR で複数 API を毎分すべて取得すると、一般的な時間当たりの上限を超える可能性がある。レート制限ヘッダー、Retry-After、バックオフに従い、更新遅延を表示する。PR メタデータを先に比較し必要な詳細のみ取得する案を実測する。変更指標だけでイベントを取りこぼさないかも検証する。

一般 Events API は履歴件数・期間と遅延に制約があり、正確な更新検知の唯一の情報源にしない。ポーリング間で発生し消えた一時的な変更の完全検知は未保証。強い保証が必要なら webhook 等が別途必要になる。

一次資料: [タイムライン](https://docs.github.com/en/rest/issues/timeline)、[イベント種別](https://docs.github.com/en/rest/using-the-rest-api/issue-event-types)、[レビュー](https://docs.github.com/en/rest/pulls/reviews)、[インラインコメント](https://docs.github.com/en/rest/pulls/comments)、[Events API](https://docs.github.com/en/rest/activity/events)、[レート制限](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)、[API 利用指針](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)。

## 保存とバックアップ

SQLite のトランザクションで PR 情報と利用者の記録を保存する。UI が保持する編集内容を同期結果で上書きしない。GitHub のキャッシュ、利用者の操作、観点の確認履歴、同期境界を分離する。

バックアップはバージョン・接続先とアカウント ID・利用者の記録・設定を含む JSON 等の明示的な形式を候補とする。トークンや secret は含めない。形式とアカウントを検証してからトランザクションで復元し、失敗時は既存データを維持する。SQLite の使用中ファイルを単純にコピーする方式は避け、DB 全体を出力する場合は公式 Backup API 等を使う。

一次資料: [Tauri SQL](https://v2.tauri.app/plugin/sql/)、[SQLite backup](https://www.sqlite.org/backup.html)。

## 次の実機検証

1. GitHub の認証候補と組織のアクセス制約を比較し、配布時に秘密が必要になる方式の扱いを確定する。
2. 個人・チーム依頼、レビュー後の追跡、Bot 除外、再依頼を実アカウントで確認する。
3. macOS と Linux の起動・資格情報保存・外部ブラウザ起動を試作する。
4. 約30件の起動時間と API 呼び出し量を計測し、8時間の連続利用検証につなげる。

## OpenSpec 検証

公式の spec-driven 構成で proposal・specs・design・tasks を作成した。CLI は環境に未導入だったため、一時 npm キャッシュに OpenSpec 2.1.0 を取得した。`openspec validate add-review-workspace --strict` は成功した。アプリ実装・実アカウント疎通・OS 実機試験は未実施。

一次資料: [標準スキーマ](https://openspec.dev/docs/schemas/spec-driven)、[CLI](https://openspec.dev/docs/cli)。
