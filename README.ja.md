# hermes-even-ai-plugin

**[Even Realities G2](https://www.evenrealities.com/) スマートグラスの HUD アダプタ — [Hermes Agent](https://github.com/NousResearch/hermes-agent) 用、Even App の「Add Agent」機能経由。**

G2 スマートグラス越しに Hermes エージェントと話せます。約 28 秒以内の応答は HUD にインライン表示、それ以上かかる長文は Telegram にフォールバックされて取りこぼしなし。

> 言語: [English](./README.md) | 日本語
>
> ステータス: **alpha** — 実機（Even G2 + Even App + Hermes Agent v0.13.0）で動作確認済みですが、Even App 側のプロトコルは未公開でリバースエンジニアリングしたものです。今後の Even App のアップデートで壊れる可能性があります。バグ報告は歓迎です。

---

## なぜこれを作ったか

Even App には HTTPS URL と bearer token を入れるだけで「Add Agent」できる機能があります。これは G2 の音声入力を OpenAI 互換の chat-completion `POST` リクエストとして転送して、応答を HUD に表示する仕組みです。

つまり Even App は、すでに Hermes で動いている任意のエージェントのフロントエンドとして使える、ということです。Hermes がそのワイヤーフォーマットを話せればいいだけ。このプラグインはそのブリッジです。

接続後の動作：

- G2 の音声入力が Hermes に first-class platform として届きます（Telegram / Discord / LINE などと同列）。session・memory・skills・MCP すべて維持されます。
- 短い応答は Even App の約 30 秒の HUD 表示 deadline 内に表示されます。
- 長い応答（slow LLM、tool 呼び出しチェーン）は Telegram にオーバーフローして、必ず受け取れます。
- `hermes cron deliver=even-ai` の cron ジョブも Telegram fallback 経由（Even AI 自体には server push チャネルがないため）。

---

## 機能

- **First-class な Hermes platform** — `ctx.register_platform` 経由で `even-ai` として登録。`hermes status` にも Telegram などと並んで表示されます。
- **Hedged-request 対応** — Even App は 1 ターンにつき 2 つの並列 POST を送り、先に返ってきた応答を採用します。アダプタは両 POST で同じ in-flight future を共有するので、本物の応答が必ず勝ちます。
- **二段構成の slow-LLM UX** — 28 秒以内なら本物の応答を inbound POST に乗せる。超えた場合は placeholder を POST に返し、本物の応答は Telegram fallback 経由で届けます。
- **G2 向けのフォーマット処理** — markdown を除去（`**bold**` やコードブロック・リスト記号が HUD に文字として残らない）、400 文字で文末境界を意識した truncate、改行はそのまま反映。
- **proactive 送信の Telegram fallback** — skill / cron / 別 session からの `send()` 呼び出しは Telegram 経由（Even AI は request/response のみで server push なし）。
- **debug echo モード内蔵** — `EVEN_AI_DEBUG_ECHO_DELAY=N` で N 秒待ってから固定応答を返すモード。Even App のクライアント挙動を再測定したいとき（Even App のアップデート時など）に便利です。

---

## アーキテクチャ

```
[Even G2]            (BLE)
   │
   ▼
[iPhone Even App "Add Agent"]
   │      HTTPS POST × 2 (hedged request, 1-2ms 差)
   │      Authorization: Bearer <token>
   │      x-openclaw-agent-id: <agent-id>
   ▼
[even-ai-platform プラグイン, port 8767]
   │      chat_id ごとに future を共有
   │      ├─ POST #1: dispatch → handle_message → future を await
   │      └─ POST #2: 重複検出 → 同じ future を共有 → 同じ content を返す
   ▼
[Hermes Gateway Runner → default profile]
   │
   ▼
OpenAI 互換 chat.completion JSON (non-streamed)
   │
   ▼
[両 POST に←] 400 文字に truncate、markdown 除去
   │
   ▼
[Even App] 先着の応答を採用
   │
   ▼
[G2 HUD] 576×136 モノクロ、プレーンテキスト、\n は機能

応答が 28 秒を超えたとき:
   ├─ POST に「考え中…（placeholder）」を 28 秒で返却
   └─ バックグラウンドタスクが本物の応答を await（最大 600 秒）
        └─ Telegram Bot API → ユーザーの Telegram (push notification)
```

---

## 動作要件

- **Hermes Agent v0.13.0** 以降の互換版（2026-05-11 動作確認）。古い版だとこのアダプタが使う platform-registration hook が無い可能性があります。
- **Python 3.11+**（Hermes 本体が既に要求）。
- **`aiohttp`** — Hermes に同梱されているので追加 install 不要。
- **Even Realities G2** グラス本体と、Even AI の「Add Agent」が見える iOS の Even App。
- **port `8767` を覆う HTTPS リバースプロキシ** — Even App は plain HTTP の URL を拒否します。選択肢:
  - Tailscale Funnel
  - Cloudflare Tunnel
  - Caddy / nginx + 実 TLS 証明書
- **（任意・推奨）Telegram bot** — Hermes ですでに使っているものと同じで OK。長文応答や proactive 送信が HUD の予算をオーバーしたときの逃げ場として使います。

---

## インストール

### 1. Hermes のプラグインディレクトリに配置

```bash
# どちらでも好きな方:
#   (a) user-plugins ディレクトリに直接 clone
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/.hermes/plugins/even-ai

#   (b) 別の場所に clone して symlink
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/src/hermes-even-ai-plugin
ln -s ~/src/hermes-even-ai-plugin ~/.hermes/plugins/even-ai
```

ディスク上のディレクトリ名（`even-ai`）はプラグイン名（`even-ai-platform`）と独立で構いません。Hermes は `plugin.yaml` を読んで本当のプラグイン名を判定します。

### 2. プラグインを有効化

```bash
hermes plugins enable even-ai-platform
```

これで `~/.hermes/config.yaml` の `plugins.enabled` に `even-ai-platform` が追加されます（user plugins は opt-in がデフォルト）。

### 3. 環境変数を設定

`~/.hermes/.env` に追加（最低限）：

```bash
# 必須
EVEN_AI_AUTH_TOKEN=<ランダムな長い文字列を自分で決める>

# 開発時は必須（Even App の "agent-id" は実 Hermes user_id ではないので、
# Hermes 側に「受け付けていい」と伝える必要があります）
EVEN_AI_ALLOW_ALL_USERS=true

# 任意・推奨
EVEN_AI_BIND_PORT=8767
EVEN_AI_RESPONSE_TIMEOUT=28
EVEN_AI_HOME_CHAT_ID=even-ai-main
EVEN_AI_TELEGRAM_FALLBACK=true
EVEN_AI_TELEGRAM_FALLBACK_PREFIX=👓 
```

全項目は下の [**設定**](#設定) を参照。

### 4. HTTPS フロントエンドを用意

このプラグインは port `8767` で plain HTTP を bind します。前段に HTTPS を置いてください。Even App は plain HTTP の URL を Add Agent ダイアログで拒否します。疎通確認：

```bash
curl -sS https://<your-public-host>/health \
  -H "Authorization: Bearer $EVEN_AI_AUTH_TOKEN"
# → {"status":"ok","platform":"even-ai","max_chars":400,"response_timeout":28.0}
```

### 5. Hermes Gateway を再起動

```bash
# macOS launchd
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# 他の起動方法を使ってる場合はそちらで
hermes restart
```

確認：

```bash
hermes status
# → ... even-ai connected ✓
```

### 6. iPhone の Even App で Agent を追加

iOS の Even App で：

1. Even AI → Add Agent
2. **URL**: `https://<your-public-host>/` （path はサーバーのルート）
3. **Token**: `EVEN_AI_AUTH_TOKEN` と同じ値
4. **Name** / **Description**: 好きに。表示用なので任意
5. 保存

G2 に話しかける（"Hey, Even"）と Hermes のエージェントに届きます。HUD に応答が表示されます。

---

## 設定

| 環境変数 | デフォルト | 説明 |
|---|---|---|
| `EVEN_AI_AUTH_TOKEN` | （必須） | Even App が送るべき bearer token。長いランダム文字列を自分で決めてください。 |
| `EVEN_AI_BIND_HOST` | `0.0.0.0` | inbound HTTP サーバーの bind address。 |
| `EVEN_AI_BIND_PORT` | `8767` | bind port。`8765`/`8766` は他のツールと衝突する可能性あり。 |
| `EVEN_AI_ALLOWED_AGENT_IDS` | （any） | カンマ区切りで `x-openclaw-agent-id` の許可リスト。空なら全許可。 |
| `EVEN_AI_ALLOW_ALL_USERS` | `false` | `true` にすると agent-id ヘッダがそのまま Hermes user として受け付けられます。**開発時は必須**：Even App の agent-id は Hermes に登録された user_id ではないため。 |
| `EVEN_AI_HOME_CHAT_ID` | `even-ai-main` | `hermes cron deliver=even-ai` や proactive 送信で使う既定の chat_id。 |
| `EVEN_AI_MAX_CHARS` | `400` | HUD 応答の最大文字数。G2 のディスプレイは 576×136 モノクロで、約 400 文字が実用上限です。 |
| `EVEN_AI_RESPONSE_TIMEOUT` | `28` | inbound POST で placeholder を返すまでの秒数。**30 を超えると HUD は Even App 自前の英語 "wait" overlay に上書きされる**ので、原則 28 のまま。詳細は [背景](#背景) 参照。 |
| `EVEN_AI_SLOW_PLACEHOLDER` | `考え中… 続きは Telegram に届けるね。` | timeout 超過時に POST へ返す placeholder。英語に変えたい場合はここで上書き。 |
| `EVEN_AI_TELEGRAM_FALLBACK` | `true` | proactive 送信と長文オーバーフローを Telegram 経由でルーティングするかどうか。`false` で破棄。 |
| `EVEN_AI_TELEGRAM_CHAT_ID` | (`TELEGRAM_HOME_CHANNEL`) | Telegram fallback の chat_id。未設定なら Hermes 側 Telegram adapter のデフォルト値を継承。 |
| `EVEN_AI_TELEGRAM_FALLBACK_PREFIX` | (なし) | Telegram fallback メッセージの先頭に付ける prefix。例 `👓 ` で通常の Telegram 応答と区別しやすくなります。 |
| `EVEN_AI_DEBUG_ECHO_DELAY` | `0` | **デバッグ用**。0 より大きい値にすると、アダプタはエージェントに dispatch せず N 秒待って固定 echo を返します。Even App のクライアント timeout / retry の挙動測定に。本番では 0 のまま。 |

---

## 動作モード

### 短文応答（28 秒以内）

```
G2: "Hey Even, 今何時？"
  → Even App POST × 2 (hedged) → アダプタ → handle_message →
    エージェントが 1.2 秒で "14:32 だよ" と返す →
  → 両 POST に同じ content →
  → Even App は先着を採用 →
  → HUD: "14:32 だよ"
```

### 長文応答（28 秒超 — slow LLM / tool 呼び出しチェーン）

```
G2: "新しい記憶システムのアーキテクチャを説明して"
  → POST × 2 → アダプタ → handle_message →
  → 28 秒経過、エージェントまだ考え中 →
  → POST は "考え中… 続きは Telegram に届けるね。" を返却 →
  → HUD: "考え中… 続きは Telegram に届けるね。" (placeholder)
  → エージェントが 12 秒後に応答完成 →
  → バックグラウンドタスクが本物の応答を取得 →
  → Telegram Bot API → ユーザーの Telegram → push 通知 →
  → ユーザーはフル回答をスマホで読む
```

### proactive 送信（skill / cron / 別 session）

```
Skill: send("even-ai-main", "15時のミーティング忘れないでね")
  → pending POST なし → Telegram fallback 経路 →
  → Telegram Bot API → ユーザーの Telegram → push 通知
```

これは意図的な設計です。Even AI のワイヤープロトコルには server-push チャネルがないので、pending POST がないときにユーザーへ届ける唯一の方法は別 platform 経由。Telegram は多くの Hermes 利用者がすでに設定済みなので fallback として推奨。

---

## トラブルシューティング

### HUD に何も出ない / Even App が読み込み中のままになる

- **`401 unauthorized`** — token 不一致。`EVEN_AI_AUTH_TOKEN` と Even App の Add Agent ダイアログで入れた token を確認。アダプタは 401 のとき `provided_len=X expected_len=Y` をログに出すので、貼り付けミス（前後空白など）が一目でわかります。
- **TCP 疎通** — LAN 外のマシンから `curl -sS https://<host>/health -H "Authorization: Bearer $TOKEN"` を叩いて確認。これで失敗したら HTTPS フロントエンドが届いていない。
- **plain HTTP** — Even App は非 HTTPS URL を拒否します。port 8767 の前に **必ず** TLS を置いてください。
- **エージェントログに `Unauthorized user: ... Dropping message from unauthorized user`** — `EVEN_AI_ALLOW_ALL_USERS=true` を設定。Even App の `x-openclaw-agent-id` は実 Hermes user_id ではなく、gateway のデフォルト user-auth チェックが弾きます。

### HUD にエージェントの応答ではなく英語の "wait" 表示が出る

- 応答が約 30 秒を超えました。Even App には未公開の HUD 表示 deadline が 30-32 秒にあり、それを超えるとサーバーの 200 が届いていても自前の wait overlay で上書きされます。
- `EVEN_AI_RESPONSE_TIMEOUT` を確認: **必ず 28 以下**（安全上限）。プラグインはこの値を超えると HUD に placeholder を出すので、それが本来の動作です。
- reasoning モデルなどで頻繁にオーバーする場合は、Even AI session 向けに軽量モデルへ切替を検討。

### HUD に placeholder は出るが Telegram に本物が届かない

- `EVEN_AI_TELEGRAM_FALLBACK=true`（デフォルト）で、`EVEN_AI_TELEGRAM_CHAT_ID` か `TELEGRAM_HOME_CHANNEL` のどちらかが設定されている必要あり。
- Telegram の `TELEGRAM_BOT_TOKEN` env が Hermes 環境にある必要あり（プラグインは out-of-process 経路で `tools.send_message_tool._send_telegram` を import します）。
- エージェントログで `[EvenAI] direct Telegram send failed` か `[EvenAI] no TELEGRAM_BOT_TOKEN for fallback` を確認。

### `Plugin 'even-ai-platform' has no register() function`

- `__init__.py` で `register` を re-export する必要があります。中身が以下になっているか確認：

  ```python
  from .adapter import register
  __all__ = ["register"]
  ```

- このパッケージは元から正しく書いてあります。カスタムするときも消さないでください。

### Hedged-request の placeholder が HUD に出てしまう

- これは `_handle_post` を「先勝ち placeholder 戦略」に書き換えたときに起きます。**やらないでください**。Even App は 1 ターンにつき 2 並列 POST を送り、先着を採用します。片方の POST が即 placeholder を返したら、それが勝って本物の応答は破棄されます。
- 同梱の実装はこれを正しく扱っています：第二 POST は同じ `chat_id` の in-flight future を検出して `asyncio.shield` で共有するので、両 POST が同じ content を返します。

---

## 背景

このセクションでは、アダプタが回避する必要のある Even App の挙動を文書化します。すべての数値は 2026-05-11 に Hermes Agent v0.13.0 と実機で実測したものです。

### ワイヤーフォーマット

```
POST <root>
Authorization: Bearer <token>
Content-Type: application/json
User-Agent: Dart/3.8 (dart:io)
x-openclaw-agent-id: <agent-id>          # 例: "main"

{"model":"openclaw","messages":[{"role":"user","content":"..."}]}
```

- **レスポンス**: non-streamed の OpenAI 互換 `chat.completion` JSON。SSE は **不要**。
- `content` 内の **`\n`** は HUD 上で改行として機能します。
- path は **サーバーのルート**で、`/v1/chat/completions` ではありません。

### Hedged-request パターン

Even App は 1 ターンにつき **2 並列 POST** を送ります。1-2 ms 差で、ヘッダ・body は完全に同一。先着の応答を採用して、後から来たほうは黙って破棄します。

実装上の帰結：

1. アダプタは **両方の POST に同じ content** を返す必要があります（さもないとユーザーが古い応答や空応答をランダムに見ます）。
2. 「2 個目の POST が来たら placeholder を返して、1 個目は計算継続」みたいな素朴な戦略は逆効果です。placeholder のほうが速いので placeholder が勝ち、本物が捨てられます。同梱コードは `asyncio.shield(existing)` で両 POST が同じ future を await します。

### 表示 deadline（TCP timeout ではない）

| N (秒) | TCP socket | HUD 表示 |
|---|---|---|
| 5 | 切れない | 本物 ✅ |
| 10 | 切れない | 本物 ✅ |
| 15 | 切れない | 本物 ✅ |
| 25 | 切れない | 本物 ✅ |
| 28 | 切れない | 本物 ✅ |
| 30 | 切れない | 本物 ✅ |
| **32** | 切れない | 英語 "wait" overlay ❌ |
| 45 | 切れない | 英語 "wait" overlay ❌ |

2 種類の timeout が別々に存在します：

- **TCP クライアント timeout** — 45 秒超。socket は表示 deadline を大きく超えても閉じない（`request.transport.is_closing()` は `True` にならない）。
- **表示 deadline** — 30-32 秒。Even App はこの時刻を超えると、サーバーが HTTP 200 を返していても HUD を自前の英語 wait overlay に切り替えます。

プラグインは `EVEN_AI_RESPONSE_TIMEOUT=28` をデフォルトにして、30 秒の表示カットオフに対して 2-4 秒のマージンを確保しています。30 を超えると HUD が wait overlay に上書きされて、応答が消えます。

### Even App 新バージョンでの再測定

Even App はクローズドソースなので、挙動が変わる可能性があります。再測定の手順：

```bash
# アダプタを「N 秒待って echo」モードに：
EVEN_AI_DEBUG_ECHO_DELAY=10  hermes restart

# G2 に話しかけて HUD と Hermes ログを観察。
# N = 5, 10, 15, 25, 28, 30, 32, 45 で繰り返す。
# 新しい表示 deadline に応じて EVEN_AI_RESPONSE_TIMEOUT を調整。
# EVEN_AI_DEBUG_ECHO_DELAY=0 に戻して本番運用に復帰。
```

アダプタは debug echo モード中に hedged-request 検出・TCP disconnect タイムスタンプ・`retry_seen` フラグをログに出すので、設定した delay と相関付けて確認できます。

---

## 互換性

- **動作確認済み**: Hermes Agent v0.13.0、macOS launchd の Hermes gateway、iOS Even App（Even AI 機能）、Even Realities G2 firmware（2026-05-11 時点）。
- **未検証だが動くはず**: Linux Hermes installs、Hermes Agent v0.12.0+（このプラグインが使う `register_platform` API は安定）。
- **動かないと判明**: Hermes Agent v0.12.0 より前 — platform-plugin 拡張ポイントがない。

---

## コントリビューション

バグ報告・PR 歓迎です。特に以下：

- 新しい Even App バージョンでの実測値（表示 deadline の数値が変わるパターン）。
- Linux デプロイの記録（プラグインは動くはずだけど検証環境は macOS）。
- オーバーフロー経路の他 platform fallback（Discord / LINE / Slack）。

コードベースは小さく（`adapter.py` 1 つ、約 1000 行）自己完結しています。`test_formatting.py` は markdown 除去と sentence-aware truncation をカバーしているので、フォーマット変更のときはここに test ケースを足してください。

---

## ライセンス

[MIT](./LICENSE).

---

## 変更履歴

リリース履歴は [CHANGELOG.md](./CHANGELOG.md) を参照。

---

## 関連リンク

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — このプラグインが組み込まれるエージェントプラットフォーム。
- [awesome-hermes-agent](https://github.com/0xNyk/awesome-hermes-agent) — コミュニティのプラグイン / ツールリスト。
- [Even Realities G2](https://www.evenrealities.com/) — グラス本体。
- [Hermes — Adding a Platform Adapter](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/adding-platform-adapters.md) — このプラグインを書くために参照した開発ガイド。
