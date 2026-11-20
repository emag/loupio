# 対応OS・配布形式と資格情報ストア

## MVP の対応範囲

| OS | 初版の対象 | 配布形式 | 資格情報ストア |
| --- | --- | --- | --- |
| macOS | 15 Sequoia を最低バージョンとし、15・26 Tahoe・27 Golden Gate を検証対象にする。各 OS が対応する Apple Silicon・Intel で検証 | 署名・公証済みの Universal `.app` を `.dmg` で直接配布 | Keychain Services の端末内の Generic Password 項目 |
| Linux | Ubuntu 24.04 LTS、Debian 13、開発端末の NixOS 26.05。まず x86_64 | Ubuntu・Debian は `.deb`。NixOS はネイティブに依存を解決する Nix パッケージ | ユーザーのデスクトップセッションの Secret Service。初回の検証実装は GNOME Keyring |

macOS の最低バージョンと Linux のディストリビューションは MVP の対応範囲として選んだ。実行可能な全環境を示す値ではない。Linux の `.deb` は WebKitGTK 4.1 と GTK 3 を使い、Ubuntu 24.04 をビルド基準として Ubuntu と Debian の両方で依存解決と起動を検証する。NixOS は今回の開発端末で資格情報ストアを検証し、Tauri のビルドと起動はネイティブパッケージで別途確認する。各配布物の生成・署名・インストール試験は未実施。 [Apple のリリース情報](https://support.apple.com/en-us/100100) · [Tauri DMG](https://v2.tauri.app/distribute/dmg/) · [署名と公証](https://v2.tauri.app/distribute/sign/macos/) · [Tauri Debian パッケージ](https://v2.tauri.app/distribute/debian/) · [Ubuntu 24.04 LTS](https://documentation.ubuntu.com/release-notes/24.04/) · [Debian 13](https://www.debian.org/releases/trixie/)

## 保存と再取得の試作

```sh
python3 scripts/github-api-probe.py --credential-probe --client-id CLIENT_ID
```

Linux では libsecret の `secret-tool` と稼働中の Secret Service を利用する。NixOS などで実行ファイルが PATH にない場合は `LUPIO_SECRET_TOOL` にパスを指定できる。macOS では Xcode Command Line Tools の `swift` で Security.framework を呼び出す。どちらも試作の依存であり、配布するアプリはネイティブ API を Rust 側から呼び出す構成を候補とする。 [Secret Service 仕様](https://specifications.freedesktop.org/secret-service/) · [Apple Keychain Services](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains)

Device Flow 承認後、アクセストークンと付与された更新トークンを試作専用の一意の項目に保存する。秘密は標準入力経由で渡し、引数、画面、作業ファイルには出力しない。別の Python プロセスが項目から再取得し、アクセストークンで GitHub の `GET /user` を実行する。完了後はその試作項目を削除する。資格情報ストアが利用不能なら失敗を返し、平文保存には切り替えない。更新トークンの交換はこの試験の範囲外。 [GitHub App のユーザーアクセストークン](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)

### 実測結果

NixOS 26.05 x86_64 のユーザーセッションで、GNOME Keyring のログイン用コレクションに試作用 GitHub App の Device Flow で発行されたアクセストークンと更新トークンを保存した。別プロセスで再取得でき、`GET /user` は HTTP 200（0.51秒）。試作項目の削除も成功した。再ログイン後と OS 再起動後の再取得は未試験。

macOS 26.6.2（build 25G83、arm64、Apple Swift 6.4.2）で利用者が実機試験を実施した。Device Flow のアクセストークンと更新トークンを Keychain に保存し、別プロセスで再取得できた。再取得したアクセストークンによる `GET /user` は HTTP 200（0.45秒）。試作項目の削除は成功し、終了コードは 0 だった。

各対象 OS での配布物のインストールと、署名済み `.app` の再起動後に Keychain から再取得できるかは、アプリ実装後の実機検証で確認する。
