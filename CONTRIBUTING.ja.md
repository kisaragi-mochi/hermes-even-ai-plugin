# hermes-even-ai-plugin への貢献

貢献を検討いただきありがとうございます。このガイドは、プラグインに対する変更を開発・提出するための手順をまとめています。

エンドユーザー向けのセットアップは [README](./README.ja.md) を参照してください。英語版は [CONTRIBUTING.md](./CONTRIBUTING.md) です。

## スコープ

このプラグインは、Even App の「Add Agent」機能を Hermes セッションに橋渡しする **Hermes Agent プラットフォームアダプタ** です。スコープ内：

- inbound HTTPS ハンドラ、hedged-request 処理、Telegram fallback 経路、G2 向けフォーマット処理の修正・改善。
- 新しい Hermes Agent / Even App バージョンとの互換性対応。
- ドキュメント改善（英語・日本語）。

スコープ外（上流に issue を立ててください）：

- Hermes Agent コア本体への変更 → [Hermes Agent](https://github.com/NousResearch/hermes-agent)。
- Even App / G2 firmware への変更 → ベンダーのサポート窓口。

## 開発環境のセットアップ

### 1. clone して Hermes に symlink

```bash
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/src/hermes-even-ai-plugin
ln -s ~/src/hermes-even-ai-plugin ~/.hermes/plugins/even-ai
```

symlink にしておくと、編集してそのまま Gateway 再起動で反映できます（ファイル再コピー不要）。

### 2. プラグインを有効化

```bash
hermes plugins enable even-ai-platform
```

user plugin は opt-in（bundled は auto-load、user は明示的に有効化が必要）。

### 3. 環境変数を設定

`~/.hermes/.env` に環境変数を書きます（一覧は [README — 設定](./README.ja.md#設定)）。開発時は最低限：

```bash
EVEN_AI_AUTH_TOKEN=<ランダムな長い文字列>
EVEN_AI_ALLOW_ALL_USERS=true
EVEN_AI_BIND_PORT=8767
EVEN_AI_RESPONSE_TIMEOUT=28
EVEN_AI_TELEGRAM_FALLBACK=true
```

### 4. Gateway を再起動して確認

Hermes はプロセス開始時に 1 回だけ plugin code を読み込みます。編集後は **Gateway を再起動**してください：

```bash
# macOS launchd
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# 環境に合わせて
hermes restart
```

そして `~/.hermes/logs/agent.log` を確認：

```bash
tail -F ~/.hermes/logs/agent.log
```

`connecting to even-ai... ✓ even-ai connected` が出ていて、再起動後にエラーがないことを確認します。

### 5. 実機 G2 で smoke test（任意、推奨）

G2 と Even App があれば、短いプロンプトを話しかけて HUD にエージェント応答が出ることを確認してください。実機がない場合はメンテナがマージ前に確認しますので、PR description に書いてください。

## 変更の作り方

### ブランチ命名

`issue-<N>-<short-desc>` 形式。例: `issue-12-fix-401-whitespace`。ブランチ・発端 issue・merge commit の対応がそのまま分かります。

### コミット形式

Conventional Commits（[参考](https://www.conventionalcommits.org/)）：

- `feat: ...` — 新しい挙動 / 新環境変数 / 新エンドポイント。
- `fix: ...` — バグ修正。
- `docs: ...` — README / CHANGELOG / CONTRIBUTING など。
- `chore: ...` — 整理（依存更新・フォーマットなど）。

1 つの論理変更につき 1 コミット、を心がけてください。

### EN / JP README の同時更新

このリポジトリは英語・日本語の README を並列管理しています。**どちらか一方を変更したら、もう一方も同じ commit で更新してください**。push 前のセルフチェック：

```bash
git diff main..HEAD --stat | grep -E '(README\.md|README\.ja\.md)'
```

両方出ているか、両方出ていないか、のどちらかになっていること。

### 個人設定を commit しない

`git diff main..HEAD --stat` に `.env` / 個人のホスト名やトークンが入ったファイルが含まれていないか push 前に確認してください。

## Pull Request の出し方

1. **まず issue を立てるか拾ってください**。trivial でない変更は、コードを書く前に issue で議論したほうがやり直しを防げます。
2. **ブランチを push して PR を開く**。リポジトリの PR テンプレートで description が自動で埋まります — 出てきた構造をそのまま使って空欄を埋めてください。
3. **issue をリンク** — `Closes #N` でマージ時に自動 close。
4. **Test plan チェックリストを埋める** — 確認済の項目に ✓、該当しない項目は削除。
5. **レビュアーは 1 名で十分** — メンテナが diff + チェックリストを確認してマージします。CI は現在未設定です。

## バグ報告 / 機能要望

issue テンプレートを使ってください：

- [バグ報告 (English)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=bug_report.yml)
- [バグ報告 (日本語)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=bug_report.ja.yml)
- [機能要望 (English)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=feature_request.yml)
- [機能要望 (日本語)](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/new?template=feature_request.ja.yml)

バグ報告テンプレートは Hermes / Python / プラグイン / Even App のバージョン、ホスト OS、HTTPS フロントエンドを聞きます — 症状が組み合わせで変わるので、可能なら埋めてください。

## 行動規範

本プロジェクトへの参加は [Contributor Covenant Code of Conduct](./CODE_OF_CONDUCT.md)（英語、[公式日本語訳](https://www.contributor-covenant.org/ja/version/2/1/code_of_conduct/) あり）に従います。貢献にあたってはこの規範に同意することになります。

## ライセンス

このリポジトリに貢献することで、あなたの貢献が [MIT License](./LICENSE) の下で配布されることに同意することになります。
