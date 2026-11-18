# GitHub 認証・定期取得の試作

## 1.1 認証方式の比較

| 候補 | アカウント・アプリの設定 | 組織と配布への影響 |
| --- | --- | --- |
| OAuth App の authorization code + PKCE | OAuth App を登録し、callback URL と PKCE S256 を設定。コード交換には client secret が必要 | 配布端末から秘密を守るには交換サービスが必要。非公開リポジトリ用の `repo` は書き込みを含む。組織の OAuth App ポリシーに従う |
| OAuth App の device flow | OAuth App を登録し、device flow を有効化。ブラウザで利用者がコードを入力して承認。端末上でのコード交換に client secret は不要 | サーバーなしで運用可能。OAuth のスコープと組織承認の条件は authorization code と同じ |
| GitHub App の user access token + device flow | GitHub App を登録し、device flow と期限付きトークンを有効化。ブラウザで利用者が承認。発行・更新とも client secret 不要 | 組織へのインストールと対象リポジトリの許可が必要。トークンは利用者とアプリの双方がアクセスできる範囲に限定。Web application flow を選ぶ場合は PKCE 付きでもコード交換に client secret が必要 |

サーバーなしで読み取り権限を限定できる **GitHub App の user access token + device flow** を次の検証候補とする。リポジトリの Pull requests の read から始める。タイムラインと会話コメントもこの権限で参照できる。公式仕様上、`GET /user/teams` は GitHub App の user access token に追加権限を要求しない。インストールトークンとは利用主体が異なるため、本人のチーム取得には user access token を使う。対象組織での検証結果に基づき、認証の実装候補は GitHub App の Device Flow とする。 [OAuth App の認証](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) · [OAuth スコープ](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) · [GitHub App の認証](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app) · [トークン更新](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens) · [チーム取得](https://docs.github.com/en/rest/teams/teams#list-teams-for-the-authenticated-user) · [タイムライン](https://docs.github.com/en/rest/issues/timeline) · [会話コメント](https://docs.github.com/en/rest/issues/comments#list-issue-comments)

既存の GitHub CLI 認証で `GET /user` と `GET /user/teams` が成功し、8チームを取得した。既存トークンのスコープは `gist, read:org, repo`。個人宛て7件、選択チーム宛て50件はいずれも非公開リポジトリの検索で確認した。同じアカウントの別の2チームは検索結果0件だった。これで確認できたのは既存トークンでの可視範囲。Lupio App での権限と取得結果は下記に記録する。

試験対象組織でアカウントの所属は `active`、ロールは `admin` で、組織は2要素認証を必須にしていた。REST API が返した契約プランは `team`。組織レベルの SAML SSO は Enterprise Cloud の機能で、GraphQL にも組織の SAML ID プロバイダーは返らなかった。GitHub App のインストール制約は、組織設定の Member privileges にある GitHub Apps と App access requests で確認する。 [GitHub の SAML 要件](https://docs.github.com/en/enterprise-cloud@latest/organizations/managing-saml-single-sign-on-for-your-organization) · [アプリのインストール制約](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/limiting-oauth-app-and-github-app-access-requests-and-installations)

対象組織では、GitHub App のインストール申請の可否、承認者、対象リポジトリ、SAML SSO セッションの要否を確認する。OAuth App を選ぶ場合は第三者 OAuth App の許可リストとオーナー承認も確認する。既知の非公開 PR を直接取得し、検索0件とアクセス制限を区別する。GitHub App では `GET /user/installations` とそのインストールでアクセス可能なリポジトリも照合する。SAML を使う組織でアクセスが欠ける場合は、ブラウザで組織の有効な SAML セッションを確立してから認可する。 [OAuth App と組織](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps) · [組織のインストール・申請制限](https://docs.github.com/en/enterprise-cloud@latest/organizations/managing-programmatic-access-to-your-organization/limiting-oauth-app-and-github-app-access-requests-and-installations) · [GitHub App インストール API](https://docs.github.com/en/rest/apps/installations)

### Device Flow での試験

既存の GitHub CLI トークンで確認したところ、試験には非公開リポジトリ1件で足りる。このリポジトリには個人宛て2件と選択チーム宛て33件があった。

組織所有の非公開 GitHub App を登録し、Device Flow を有効にする。Webhook は無効、リポジトリ権限は Pull requests の Read-only と自動付与の Metadata に絞る。組織・ユーザー権限は追加せず、上記の非公開リポジトリを選択してインストールする。

```sh
python3 scripts/github-api-probe.py --team ORG/SLUG --client-id CLIENT_ID
```

表示されたユーザーコードを GitHub のブラウザ画面で入力する。スクリプトはユーザーアクセストークンをメモリ上で使い、所属チーム、インストール権限、許可済み非公開リポジトリを照合する。個人・チームの両検索からそれぞれ1件以上について、現在の依頼先と非公開PR本体を確認する。実アカウントでの実行には、インストール済み App の Client ID とブラウザでの利用者承認が必要。 [Device Flow の GitHub 公式手順](https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/building-a-cli-with-a-github-app)

実アカウントで Device Flow の認証に成功した。インストール先は選択した非公開リポジトリ1件で、API が返した権限は `pull_requests: read` と `metadata: read`。個人宛て2件と選択チーム宛て33件が検索に一致し、それぞれ少なくとも1件で依頼先と非公開PR本体を直接確認した。認証後の検証は HTTP 200 が10件、6.58秒。

`GET /user/teams` は HTTP 200 だったが、応答に対象チームは含まれなかった。同じアカウントの既存 CLI トークンでは対象チームを取得できている。今回の権限では既知のチーム名による検索が可能で、所属チームから選択する機能には追加検証が必要。次の試験で組織権限の追加による違いを調べた。

既存 CLI トークンで対象チームへの所属は `active`（`maintainer`）と確認した。`GET /user/teams` の公式ドキュメントに追加権限の指定はないが、組織内のチーム一覧 `GET /orgs/{org}/teams` と個別の所属確認 `GET /orgs/{org}/teams/{team_slug}/memberships/{username}` は、Organization permissions の Members: Read-only を要求する。今回の再試験ではこの権限だけを追加し、組織のインストールで権限変更を承認した。`--check-members` は実際のインストール権限を照合し、個人・チーム宛ての非公開 PR と、3つのチーム取得経路を比較する。 [所属チーム一覧](https://docs.github.com/en/rest/teams/teams#list-teams-for-the-authenticated-user) · [GitHub App の権限一覧](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)

```sh
python3 scripts/github-api-probe.py --team ORG/SLUG --client-id CLIENT_ID --check-members
```

対象組織の管理者が Member privileges を確認した。`Allow repository admins to install GitHub Apps` は有効、`App access requests` は `Members and outside collaborators`。リポジトリ管理者によるインストールが有効でも、組織権限を要求する新規 App のインストールには組織オーナーによる対応が必要。今回の `Members: read` の権限変更は、組織のインストール側で承認した。 [対象組織の設定](https://github.com/organizations/crowdport/settings/member_privileges) · [GitHub のインストール制約](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/limiting-oauth-app-and-github-app-access-requests-and-installations)

### Members: Read-only 追加後の実測

同じ App を Device Flow で再認証し、インストール権限が `members: read`、`metadata: read`、`pull_requests: read` のみであることを確認した。許可された非公開リポジトリは1件。対象チームは `/user/teams` と組織のチーム一覧の両方に現れ、個別の所属は `active`。個人宛て2件、選択チーム宛て33件が引き続き検索に一致し、各1件以上について依頼先と非公開PR本体を直接確認した。HTTP 200 が12件、認証後の検証時間は7.58秒。

この組織では、PR の読み取り権限に `Members: read` を加えるだけで、所属チームの選択と非公開 PR の取得を両立できた。書き込み権限は付与していない。

## 1.2 と 1.4 の再現方法

Python 3 と GitHub CLI のログイン済みアカウントを使用する。対象チームは `gh api /user/teams --paginate --jq '.[] | .organization.login + "/" + .slug'` で確認できる。

```sh
python3 scripts/github-api-probe.py --team ORG/SLUG --rounds 3 --workers 4
```

個人・選択チームの依頼検索と、レビュー提出済みのオープン PR から最大30件を固定する。各回で検索を再実行し、固定した PR についてメタデータ、現在のレビュー依頼、タイムライン、会話コメント、インラインコメント、提出レビューをページ単位で取得する。初回から開始時刻を60秒間隔にそろえ、処理が60秒を超えた場合は前の回が完了してから次の回を開始する。検索は直列、PR の取得は最大4件を並行し、各PRの個別 API は順に呼ぶ。直列で比較する場合は `--workers 1` を指定する。ETag は実行中のメモリに保持し、304 では前回のデータと次ページ情報を再利用する。出力は HTTP ステータス別件数、レスポンス本文バイト数、所要時間、レート枠と依頼・追跡の集計。書き込み API は呼ばない。 [レビュー依頼 API](https://docs.github.com/en/rest/pulls/review-requests) · [REST API の条件付きリクエスト](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

## 実アカウントでの追跡結果

30件のオープン PR のうち、個人宛て7件と選択チーム宛て9件を `requested_reviewers` で確認した。この内訳には、個人とチームの両方から確認された PR も含まれる。現在の両依頼検索から外れていて、本人の提出済みレビューがある16件も、固定集合から引き続き取得できた。レビュー提出前後を連続観測するテストと、試験中の追加コミット、返信、再依頼の検知は未実施。

## API 予算の見方

### 30件の実測

| 取得条件 | HTTP 200 | HTTP 304 | 応答前の切断 | 所要時間 | 本文 | core 使用数の差分 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 直列・初回 | 183 | 0 | 0 | 120.42秒 | 4,796,358 B | +151 |
| 4件並行・初回 | 183 | 0 | 0 | 30.92秒 | 4,796,315 B | +150 |
| 4件並行・60秒後 | 3 | 180 | 4 | 30.22秒 | 760,949 B | 0 |
| 4件並行・120秒後 | 3 | 180 | 4 | 28.97秒 | 760,949 B | 0 |

本文サイズは HTTP ヘッダーと TLS の転送分を除く。使用数の差分は `/rate_limit` の同じリセット枠内のスナップショットから算出した。初回に取得した詳細180レスポンスと core 使用数の増分は一致しなかったため、予算設計では180件を上限側の見積もりに使う。4件並行の初回試行は応答前の切断で中断した。再実行では、接続が応答前に閉じた場合だけ、読み取りを1回再試行した。

4件並行では初回と定期取得の全3回が1分以内に完了した。定期取得の2回では詳細すべてが304で、core 枠の消費は観測されなかった。変更が集中する時間帯、複数ページを要する PR、複数端末、8時間連続の動作は測定していない。

### 継続利用の見積もり

30 PR × 6エンドポイントで、ページ追加なしなら詳細は180リクエスト／回。依頼検索が2リクエスト／回、計測専用の `/rate_limit` が1リクエスト／回。毎回180件が一次枠を消費すると仮定すると10,800件／時で、実測アカウントの core 枠5,000件／時を超える。すべてを毎分取得するなら、他クライアントとの共有や手動更新を考慮する前でも、平均で約83件／分以下の一次枠消費に抑える必要がある。認証済みの304は一次枠を消費しないが、HTTP通信と二次制限の対象にはなる。測定専用の `/rate_limit` は一次枠の消費対象外。検索には別枠（このアカウントでは30件／分）がある。 [REST API レート制限](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) · [条件付きリクエストの指針](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

実装では端末ごとの起動時に毎回コールド取得が集中しないよう、認証主体・URL・API バージョンなどの表現条件に結び付けて ETag と必要なデータを永続化する。更新は重複実行せず、応答のレートヘッダーを使って残枠とリセットまでの時間に収まるよう調整する。制限時には `Retry-After` またはリセット時刻に従い、失敗や遅延を表示する。取得量をさらに抑える際は、ページ数や複数端末の負荷を含め、依頼・インライン返信などの更新を取りこぼさない構成を試す。
