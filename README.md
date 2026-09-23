# Rails → Roundhouse → Spinel AOT

Rails 8.1.3.1 の Article / Comment ブログを [Roundhouse](https://github.com/rubys/roundhouse) で変換し、[Spinel](https://github.com/matz/spinel) で Linux x86-64 のネイティブバイナリ（AOT 実行ファイル）にします。

Rails 版と AOT バイナリ版の挙動一致を自動判定する差分比較テストスイート、Ruby/Spinel を含まない軽量実行用コンテナ（Dockerfile）、および全工程を検証する GitHub Actions パイプラインを備えています。

---

## 対象 Issue と達成状況

| Issue | 内容 | 状態 | 検証内容 |
|---|---|---|---|
| **#1** | Rails 8.1.3.1 ブログサンプルの生成 | 完了 | 21テスト・54アサーション成功。モデル・コントローラ・Turbo Streams 基準動作確定 |
| **#2** | 再現可能なビルド環境 | 完了 | Roundhouse v2026.9.18 / Spinel 2026.09.12 を SHA-256 検証付きで固定導入 |
| **#3** | Spinel 向け strict 変換 | 完了 | CSS / Hotwire アセットを取り込み、Roundhouse strict 変換で `out/spinel` を生成 |
| **#4** | Spinel による AOT ビルド & 永続化 | 完了 | `blog` バイナリ生成、GET /articles、422 入力検証、再起動後の SQLite 保持確認 |
| **#5** | Rails 版と AOT 版の差分比較テスト | 完了 | 同一初期 DB・別ポートで起動し、HTTP ステータス・ヘッダー・DOM・DB 最終状態を自動判定 |
| **#6** | 差分の調査・修正・制約の明確化 | 完了 | JSON 分岐 drop 警告の影響を特定・文書化。回帰テスト整備 |
| **#7** | GitHub Actions 統合パイプライン | 完了 | Rails テスト、変換、AOT ビルド、差分比較、コンテナ検証を単一 CI で一貫実行 |
| **#8** | 再現手順、比較結果、配布物の文書化 | 完了 | 本ドキュメントにて手順、制約、配布物の利用方法を完全記載 |
| **#9** | AOT 実行用コンテナ | 完了 | マルチステージ Dockerfile、Ruby なし軽量イメージ、Volume による SQLite 永続化を検証 |

---

## 固定バージョン

| コンポーネント | 固定バージョン | 備考 |
|---|---|---|
| **Ruby** | 3.4.5 | CI およびビルド環境 |
| **Rails** | 8.1.3.1 | 最新リリース（scaffold + Turbo Streams） |
| **Bundler** | 2.6.9 | |
| **Roundhouse** | v2026.9.18 | `config/toolchain.env` の SHA-256 で整合性検証 |
| **Spinel** | 2026.09.12 | `config/toolchain.env` の SHA-256 で整合性検証 |

---

## ディレクトリ構成と役割

- `blog/`: 元の Rails 8.1.3.1 アプリケーション（Article, Comment, バリデーション, ビュー, テスト）。
- `config/toolchain.env`: 固定ツールチェーンのバージョンと SHA-256 チェックサム定義。
- `out/spinel/`: Roundhouse による Spinel 向け Ruby 変換コード（Git には含めず再生成）。
- `scripts/`:
  - `test-rails.sh`: Rails アプリのモデル・コントローラテスト実行。
  - `install-toolchain.sh`: Roundhouse / Spinel のダウンロード・コンパイル・配置。
  - `prepare-assets.sh`: Tailwind CSS と Hotwire JavaScript のバンドル準備。
  - `transpile.sh`: Roundhouse による strict モード変換。
  - `build.sh`: Spinel による AOT コンパイルと `out/runtime` パッケージング。
  - `smoke-native.sh`: AOT バイナリのスモークテスト（HTTP 応答・再起動後データ保持）。
  - `compare.py`: Rails 版と AOT 版の差分比較テストスイート（Python 3 標準ライブラリ製）。
  - `run-comparison.sh`: サーバー同時起動と差分比較のオーケストレーション。
  - `test-container.sh`: Docker コンテナのビルド・永続化・ロード自動検証。
- `artifacts/`: ビルド成果物（AOT バイナリパッケージ、Docker イメージアーカイブ等）。
- `reports/`: ログ、LDD 依存、C コンパイルログ、差分レポート。

---

## ローカルでの再現手順

Ubuntu 24.04 x86-64（または WSL2 / Docker）環境で以下を実行します。

### 1. 依存ライブラリのインストール
```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  clang make libsqlite3-dev libjemalloc-dev libssl-dev zlib1g-dev sqlite3 pkg-config python3
```

### 2. Rails テストとツールチェーン導入
```bash
# Rails ベースラインテスト
bash scripts/test-rails.sh

# 固定版 Roundhouse / Spinel 導入
bash scripts/install-toolchain.sh
```

### 3. AOT バイナリのビルドとスモークテスト
```bash
# strict 変換とネイティブコンパイル
bash scripts/build.sh

# バイナリの起動・作成・再起動永続化スモークテスト
bash scripts/smoke-native.sh
```

### 4. Rails 版 vs AOT 版の差分比較テスト (#5)
```bash
bash scripts/run-comparison.sh
```
両サーバー（Rails: 33000、AOT: 38000）が同一の初期 SQLite DB で起動し、同一操作列を実行して結果を検証します。差分レポートは `reports/differential/` に出力されます。

---

## 差分比較結果と正規化規則 (#5, #6)

### 比較対象操作
1. **記事一覧 (`GET /articles`)**: 初期 3 記事の DOM 構造一致
2. **新規作成フォーム (`GET /articles/new`)**: フォーム入力要素一致
3. **作成バリデーションエラー (`POST /articles` 空入力)**: 422 Unprocessable Content およびエラーメッセージ一致
4. **記事作成正常系 (`POST /articles`)**: 302/303 リダイレクト、Location (`/articles/4`)、DB 挿入一致
5. **記事詳細 (`GET /articles/4`)**: タイトル、本文、コメントフォームの一致
6. **編集フォーム (`GET /articles/4/edit`)**: 既存値の展開一致
7. **更新バリデーションエラー (`PATCH /articles/4` 空入力)**: 422 ステータスおよびエラー表示一致
8. **記事更新正常系 (`PATCH /articles/4`)**: 302/303 リダイレクト、Location、DB 更新一致
9. **コメント作成 (`POST /articles/4/comments`)**: 302 リダイレクト、詳細画面での表示、DB 挿入一致
10. **コメント削除 (`DELETE /articles/4/comments/4`)**: 302 リダイレクト、詳細画面からの削除、DB 削除一致
11. **記事削除 (`DELETE /articles/4`)**: 302/303 リダイレクト、一覧からの削除、DB 削除一致
12. **SQLite 最終状態 (`case_12_database_equivalence`)**: `articles` / `comments` テーブルの全レコード完全一致

### 正規化規則
- **CSRF トークン**: `authenticity_token` (`<input>` および `<meta>`) は `[CSRF_TOKEN]` に置換して比較。
- **動的タイムスタンプ**: `\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}...` は `[TIMESTAMP]` に正規化。
- **DOM 意味構造比較**: `<main>` または `<body>` タグ内の意味的要素・通知（flash）を抽出し、空白・改行揺れを吸収。
- **Location ヘッダー**: ホスト/ポートを除外した相対パスで比較。

### 既知の制約と差異 (Issue #6 分析)
- **JSON 応答分岐の drop**:
  `ArticlesController` の `create` / `update` において、`format.json` 分岐に以下の警告が出ます：
  `warning[lower_residue]: respond_to arm format.json dropped (inline render json: <expr> needs an encoder this tree has none for) — this action answers every format with its html branch`
  Roundhouse の本バージョンではインライン JSON エンコーダーがモデルされていないため、`format.json` アームは HTML ブランチにフォールバックします。ブラウザによる HTML CRUD 操作は完全一致しますが、`Accept: application/json` や `.json` リクエストに対しては HTML が返されます。この挙動は `case_13_json_format_negotiation` で記録・文書化されています。

---

## AOT 実行用 Docker コンテナ (#9)

Ruby, Rails, Spinel を含まない軽量コンテナイメージを提供します。

### Dockerfile の特徴
- **マルチステージビルド**:
  - `builder`: Ubuntu 24.04 上でツールチェーンをセットアップし、AOT バイナリ `blog` をビルド。
  - `runtime`: Ubuntu 24.04 最小ベースにバイナリ、静的アセット、必要ライブラリ（`libsqlite3-0`, `libjemalloc2`）のみを配置。
- **PID 1 実行**: `docker-entrypoint.sh` から `exec /app/blog` することでシグナルを正常ハンドリング。
- **SQLite データの永続化**: `/app/storage` をボリュームとしてマウント可能。初回起動時に DB がなければ `db/seed.sql` から自動初期化。

### コンテナのビルドと起動
```bash
# 1. コンテナイメージのビルド
docker build -t example-rails-aot:latest .

# 2. ボリュームを指定して起動（ポート 3000）
docker volume create blog_storage
docker run -d \
  --name blog-app \
  -p 3000:3000 \
  -v blog_storage:/app/storage \
  example-rails-aot:latest

# 3. ブラウザでアクセス
curl http://localhost:3000/articles
```

### コンテナ検証スクリプト
```bash
bash scripts/test-container.sh
```
イメージビルド、Ruby/Spinel 不在の検証、記事作成、コンテナ再作成後のボリューム永続化確認、`docker save` と `docker load` を自動検証します。

---

## GitHub Actions パイプライン (#7)

ワークフロー [`.github/workflows/aot.yml`](.github/workflows/aot.yml) により、プッシュおよび PR ごとに以下を自動検証します。

1. **`build-and-test`**:
   - Rails ベースラインテスト (`scripts/test-rails.sh`)
   - Strict 変換 & Spinel ネイティブコンパイル (`scripts/build.sh`)
   - スモークテスト (`scripts/smoke-native.sh`)
   - **Rails vs AOT 差分比較テスト (`scripts/run-comparison.sh`)**
2. **`container-verification`**:
   - マルチステージ Docker イメージビルド
   - Ruby/Spinel 不在確認
   - ボリューム永続化スモークテスト (`scripts/test-container.sh`)
   - Docker イメージアーカイブ作成
3. **`runtime-without-ruby`**:
   - Ruby / Spinel のないクリーンな Ubuntu コンテナ内でのバイナリ直接動作検証

### 配布物 (Artifacts)
- `native-runtime-<sha>`: Linux x86-64 実行バイナリ、静的アセット、seed SQL のアーカイブ (`blog-linux-x86_64.tar.gz`)。
- `container-image-<sha>`: `docker load` 可能な Docker イメージアーカイブ (`blog-container-image.tar.gz`)。
- `diagnostics-<sha>`: ビルドログ、C コンパイルログ、差分比較結果 (`reports/differential/summary.json` 等)。

ツールチェーンの選定根拠や生成元、変換警告の詳細な記録については [provenance.md](docs/provenance.md) を参照してください。
