# Rails → Roundhouse → Spinel AOT

Rails の Article / Comment ブログを Roundhouse で変換し、Spinel で Linux x86-64 のネイティブバイナリにします。

## 対象 Issue

今回の実装範囲は #1（基準アプリ）、#2（ツール環境）、#3（変換）、#4（AOT と起動・永続化）です。Rails/AOT 全操作の差分比較は #5、実行用 Dockerfile と配布イメージは #9 で追加します。

## 固定バージョン

| ツール | バージョン |
|---|---|
| Ruby | 3.4.5 |
| Rails | 8.1.3.1 |
| Bundler | 2.6.9 |
| Roundhouse | v2026.9.18 |
| Spinel | 2026.09.12 |

ダウンロードするビルドツールは `config/toolchain.env` の SHA-256 で検証します。Rails ソースの生成元は [Roundhouse の公式スクリプト](https://github.com/rubys/roundhouse/blob/v2026.9.18/scripts/create-blog)で、`scripts/vendor` にライセンスとともに保存しています。

## 再現手順

Ubuntu 24.04 x86-64 を使用し、Ruby/Bundler/Node.js を上記の版に合わせます。システム依存:

```sh
sudo apt-get update
sudo apt-get install clang make libsqlite3-dev libjemalloc-dev libssl-dev zlib1g-dev sqlite3 pkg-config
bash scripts/test-rails.sh
bash scripts/install-toolchain.sh
bash scripts/build.sh
bash scripts/smoke-native.sh
```

`scripts/generate.sh` は新規生成用です。既存の `blog/` は上書きしません。生成後の Rails ソースと lockfile は Git にコミットし、通常の CI はそのソースを使います。

## 成果物と実行

- `blog/`: 元の Rails アプリ（記事、コメント、関連、検証、Turbo Streams）。
- `out/spinel/`: strict モードの変換結果。
- `artifacts/blog-linux-x86_64.tar.gz`: 実行ファイルと静的ファイル、DB 初期化 SQL。
- `reports/`: 診断、ビルドログ、HTTP/再起動確認結果。

```sh
mkdir -p runtime
tar -xzf artifacts/blog-linux-x86_64.tar.gz -C runtime
cd runtime
mkdir -p storage
# 新規 DB の初期化時だけ実行する。
sqlite3 storage/development.sqlite3 < db/seed.sql
PORT=3000 SPINEL_WORKERS=2 ./blog
```

実行時には libsqlite3 と jemalloc 等の共有ライブラリが必要です。正確なリンク依存は CI artifact の `reports/ldd.txt` を参照してください。Ruby/Rails/Spinel を含まない Ubuntu コンテナでも同じ実行パッケージをテストします。

## GitHub Actions

[Actions](https://github.com/koduki/example-rails-aot/actions) は Rails テスト → ツール導入 → strict 変換 → AOT → HTTP/再起動確認を実行します。元ソース、変換ソース、実行パッケージ、診断を別の artifact に保存します。

Rails 8.1.3.1 から生成した `blog/` と lockfile をコミットしています。通常の Actions はこのソースを使い、ソースの自動生成・自動 commit は行いません。CSS と Hotwire の JavaScript は固定した Rails bundle から用意し、Roundhouse の `ROUNDHOUSE_ASSETS_DIR` で生成物に含めます。

## 検証の範囲

Rails のモデル・コントローラテストと、AOT の HTTP での一覧、無効入力、作成、詳細、SQLite 保存、再起動後の読み出しを検証します。全操作の Rails/AOT 同値性、Cable の比較、Docker イメージ配布は後続 Issue の範囲です。検証の実行結果は実際の Actions run を確認してください。

現時点で create/update の JSON 分岐と未使用の mailer 宣言に変換警告が残ります。内容と影響、生成元の記録は [provenance.md](docs/provenance.md) を参照してください。
