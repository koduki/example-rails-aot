# Rails → Roundhouse → Spinel AOT

Rails **8.1.3.1** の Article / Comment ブログを生成し、[Roundhouse](https://github.com/rubys/roundhouse) で Spinel 用 Ruby に変換し、[Spinel](https://github.com/matz/spinel) で Linux x86-64 実行ファイルにします。Rails 8.1.3.1 はこの構築時点の [Rails 最新リリース](https://github.com/rails/rails/releases/tag/v8.1.3.1)です。

Roundhouse の [2026.9.18 リリース](https://github.com/rubys/roundhouse/releases/tag/v2026.9.18)と、そのリリースで検証された Spinel [2026.09.12](https://github.com/matz/spinel/releases/tag/2026.09.12)を固定しています。元の Rails アプリは Roundhouse が CI に使う `scripts/create-blog` で生成します。生成元は Rails の標準 scaffold に記事、コメント、関連、検証、Turbo Streams を追加したものです。

## 成果物

- `app/`：生成した Rails アプリ。GitHub Actions が生成後にこのリポジトリにコミットします。
- `out/spinel/`：Roundhouse の変換結果（再生成可能なので Git には含めず、Actions artifact に保存）。
- `out/spinel/build/bin/blog`：Spinel による AOT 実行ファイル（Actions artifact に保存）。
- `scripts/generate.sh`：Rails アプリの生成。
- `scripts/build.sh`：変換、AOT、HTTP スモークテスト。

## ローカルでの再現

Ruby 3.4、Rails 8.1.3.1、Node.js 22、SQLite3 CLI、clang、libsqlite3-dev、libjemalloc-dev、Roundhouse、Spinel が必要です。実行手順は [GitHub Actions の設定](.github/workflows/aot.yml)にすべて記載しています。

```bash
gem install rails -v 8.1.3.1 --no-document
bash scripts/generate.sh
bash scripts/build.sh
```

`scripts/build.sh` は `roundhouse` と `spin` が `PATH` にあることを前提にします。ブラウザーで確認する場合は `cd out/spinel && sqlite3 storage/development.sqlite3 < db/seed.sql && ./build/bin/blog` を実行し、`http://localhost:3000/articles` を開いてください。

## 検証範囲

Actions は Rails の元アプリのテスト、Roundhouse の変換、Spinel のコンパイル、生成バイナリの GET /articles を確認します。Roundhouse は Rails の任意の機能や gem を変換できるわけではありません。Rails 版と変換版の機能一致を本格的に評価する場合は、両方を起動して同じ操作とレスポンスを比較してください。
