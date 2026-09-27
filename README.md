# Rails × Roundhouse × Spinel AOT

Rails アプリを [Roundhouse](https://github.com/rubys/roundhouse) で変換し、[Spinel](https://github.com/matz/spinel) でネイティブ実行するまでを再現する実験リポジトリです。Rails のまま実行した場合、Roundhouse 変換後に Ruby で実行した場合、Spinel AOT で実行した場合の**応答の正確性と性能**を調べます。

## このリポジトリの目的

中心となる問いは「Roundhouse によって CRuby の YJIT と JRuby の JIT の効果はどう変わり、Spinel AOT の実行系全体と比べてどう見えるか」です。ベンチマークでは Rails／変換後 Ruby を CRuby（YJIT 有無）・JRuby（JRuby JIT 有無）で動かし、Spinel と比較します。JRuby の JIT 設定は JVM の JIT と別です。変換前の Rails をそのまま Spinel でビルドする経路は対象外です。

実験には二つの系統があります。

| 系統 | 役割 | 対象 |
| --- | --- | --- |
| [`blog/`](blog/) と [AOT Actions](.github/workflows/aot.yml) | Rails アプリの変換、AOT ビルド、HTTP・DB 動作比較、Ruby 不要の実行コンテナ検証 | 元アプリは Rails **8.1.3.1** |
| [`bench/`](bench/) と [Benchmark Actions](.github/workflows/benchmark.yml) | 同一 fixture と資源条件でランタイムを比較し、正確性ゲート・生データ・測定レポートを保存 | JRuby の互換性に合わせた測定用コピーは Rails **8.0.5.1** |

測定前に HTTP 応答と DB の結果を Rails 基準と比較します。性能が良く見えても、対象の操作が一致しなければ測定対象から除外します。Spinel との比較には AOT バイナリだけでなく HTTP サーバーや DB アダプターの差も含まれます。

## 調査レポートのサマリー

[初回調査レポート](docs/benchmark-results.md)は [Actions 実行 36289166814](https://github.com/koduki/example-rails-aot/actions/runs/36289166814) の**予備測定**です。測定コミットは `e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614`。GitHub hosted runner 上で、同じ3記事・3コメントの SQLite fixture を使い、`GET /articles` を各構成1回、4接続・10秒の closed-loop 方式で測定しました。以下はその試行での観測値です。

| 実行系 | ターゲット ID | JIT | Roundhouse | RPS | p50 | p95 | コンテナメモリ最大 | 判定 |
| --- | --- | --- | :---: | ---: | ---: | ---: | ---: | --- |
| CRuby / Rails | `rails-cruby-off` | Off | なし | 270.60 | 14.55 ms | 19.16 ms | 109.30 MB | passed |
| CRuby / 変換後 Ruby | `emit-cruby-off` | Off | あり | 2,268.48 | 1.72 ms | 2.54 ms | 41.58 MB | passed |
| CRuby / Rails | `rails-cruby-yjit` | YJIT On | なし | 447.53 | 8.66 ms | 13.32 ms | 133.60 MB | passed |
| CRuby / 変換後 Ruby | `emit-cruby-yjit` | YJIT On | あり | 3,021.03 | 1.29 ms | 1.99 ms | 50.16 MB | passed |
| JRuby / Rails | `rails-jruby-off` | JRuby JIT Off | なし | — | — | — | — | 診断対象・初回測定なし |
| JRuby / 変換後 Ruby | `emit-jruby-off` | JRuby JIT Off | あり | — | — | — | — | 診断対象・初回測定なし |
| JRuby / Rails | `rails-jruby` | JRuby JIT On | なし | 26.86 | 121.38 ms | 240.92 ms | 616.70 MB | passed |
| JRuby / 変換後 Ruby | `emit-jruby` | JRuby JIT On | あり | 1,258.74 | 2.48 ms | 6.22 ms | 424.90 MB | **unstable** |
| Spinel AOT | `spinel` | 対象外（AOT） | あり | 4,353.36 | 0.89 ms | 1.17 ms | 12.54 MB | passed |
| Spinel / 未変換 Rails | — | 対象外 | なし | — | — | — | — | ビルド経路なし |

この短い試行の処理量比は、Roundhouse／Rails が CRuby JIT Off で **8.38 倍**、YJIT On で **6.75 倍**、Spinel／Rails CRuby JIT Off が **16.09 倍**でした。YJIT On／Off は Rails で **1.65 倍**、変換後 Ruby で **1.33 倍**、両倍率の比は **0.805** です。JRuby JIT Off は初回測定の7構成に含まれず、JIT 有無の効果はこの表から比較できません。Roundhouse 変換後の JRuby JIT On はウォームアップが収束せず、順位付けや倍率比較に使いません。JRuby JIT Off にしても JVM JIT は有効です。メモリはコンテナ使用量でありプロセス RSS ではありません。

初回の事前比較では全9構成の5種類の読み取り経路が適格でした。その後、ベンチマーク専用コピーに限って Rails の CSRF 検証を無効化し、正常系の更新および作成・削除を別シナリオで検証できるようにしました。[CRUD 検証手順](docs/benchmark.md#benchmark-only-crud-scope)を参照してください。無効な書き込みの HTML 表示、JSON バリデーションエラー、不正 CSRF トークンの拒否には差が残り、アプリ全体の同等性は未達です。上記の予備測定の倍率は最大処理容量や JIT の因果的な寄与を示しません。専用 GCE ホストでの反復測定、open-arrival 負荷での容量探索は未実施です。条件、p95/p99、CPU、除外理由、次の検証項目は[調査レポート](docs/benchmark-results.md)に記載しています。

[後続の Actions 実行 36320457786](https://github.com/koduki/example-rails-aot/actions/runs/36320457786)では、JRuby JIT Off を含む**全9構成**で正常系の読み取り・更新と作成・削除がそれぞれ **9/9 件 `verified`** でした。各試行の DB 照合は成功し、HTTP 失敗・反復 drop はゼロです。これは低負荷の機能確認であり、上表の処理量・遅延との比較や性能倍率には使いません。[生データ](https://github.com/koduki/example-rails-aot/actions/runs/36320457786/artifacts/10932791366)を参照してください。

### 結果の採用条件

1. `preflight/preflight.json` で**そのターゲット・経路**が `eligible_endpoints` に含まれること。読み取りが通っても書き込みの適格性は得られません。
2. `trials/per-run.json` の試行状態が `passed` であること。機能確認のみの `verified`、未収束の `unstable`、`excluded`、`failed`、`not_run` を性能倍率に混ぜません。
3. 条件が揃った試行の `summary.md` と生データを併せて読むこと。固定 offered RPS の測定値から最大処理容量の倍率は算出しません。

## 人間による再現実験

リポジトリのルートから実行します。**Linux x86-64、Docker Engine、Python 3.11 以降、2つ以上の利用可能な論理 CPU**が必要です。Docker イメージのビルドにはネットワークと空きディスク容量も必要です。以下のベンチマーク経路ではホストへの Ruby インストールは不要です。

### 1. AOT アプリを起動する

Docker のマルチステージビルドで固定版の Roundhouse と Spinel を導入し、Ruby を含まない実行イメージを作ります。

```bash
git clone https://github.com/koduki/example-rails-aot.git
cd example-rails-aot
docker build -t example-rails-aot:local .
docker volume create example_rails_aot_data
docker run -d --name example-rails-aot-demo -p 3000:3000 \
  -v example_rails_aot_data:/app/storage example-rails-aot:local
curl http://127.0.0.1:3000/articles
```

作成・再起動後の SQLite 永続化やコンテナの保存・復元は `bash scripts/aot/test-container.sh` で確認できます。ローカルに Ruby 3.4.5、Bundler 2.6.9、Node.js とネイティブビルド依存を用意した場合は、以下で Rails と AOT の差分比較もできます。

```bash
bash scripts/aot/test-rails.sh
bash scripts/aot/install-toolchain.sh
bash scripts/aot/build.sh
bash scripts/aot/smoke-native.sh
bash scripts/aot/run-comparison.sh
```

差分比較は `reports/differential/` に記録されます。必要な依存パッケージと実行順は [AOT Actions](.github/workflows/aot.yml)、ツールの固定版は [`config/aot-toolchain.env`](config/aot-toolchain.env) を参照してください。

### 2. 予備ベンチマークを再実行する

CPU の指定値は環境に合わせて変更してください。`lscpu -e=CPU,CORE,ONLINE` と `taskset -pc $$` で利用可能な CPU と SMT の兄弟関係を確認し、アプリと負荷生成器に**異なる物理コア**の CPU を割り当てます。下の `0` と `2,3` は記入例です。CPU 0 が使用できない環境や兄弟スレッドが重なる環境ではそのまま実行しないでください。

```bash
APP_CPUS=0
LOAD_CPUS=2,3
python3 -m unittest discover -s tests/bench -q
python3 scripts/bench/run.py run --profile bench/profiles/smoke.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" --dry-run
python3 scripts/bench/run.py build --profile bench/profiles/smoke.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" --output bench-results/build-local-01
python3 scripts/bench/run.py preflight --profile bench/profiles/smoke.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" --output bench-results/preflight-local-01
python3 scripts/bench/run.py run --profile bench/profiles/smoke.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" \
  --preflight-file bench-results/preflight-local-01/preflight/preflight.json \
  --output bench-results/measurement-local-01
python3 scripts/bench/run.py report --output bench-results/measurement-local-01
```

`--output` には**毎回新しいディレクトリ名**を指定します。標準の smoke profile は主7構成を測定します。JRuby JIT Off の診断2構成も検証する場合は、[ターゲット一覧](bench/targets.yml)の ID を `--targets` に指定してください。事前検証の `preflight-local-01/preflight/preflight.json` で対象経路の適格性を確認してから、測定結果の `summary.md`、`summary.csv`、`trials/per-run.json`、各試行の `warmup.json` と `telemetry.json` を読みます。失敗や未収束の試行を倍率に混ぜないでください。

正常系 CRUD の機能と測定経路は同じ事前検証を使って確認できます。`crud.yml` は読み取り・更新、`crud-create-delete.yml` は作成・削除です。各試行の `database_check` が `passed` で、状態が `verified` なら HTTP と書き込み後の SQLite 状態が一致しています。これらの短時間プロファイルは性能比較のサンプルには含めません。

```bash
python3 scripts/bench/run.py run --profile bench/profiles/crud.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" \
  --preflight-file bench-results/preflight-local-01/preflight/preflight.json \
  --output bench-results/crud-local-01
python3 scripts/bench/run.py run --profile bench/profiles/crud-create-delete.yml \
  --app-cpus "$APP_CPUS" --load-cpus "$LOAD_CPUS" \
  --preflight-file bench-results/preflight-local-01/preflight/preflight.json \
  --output bench-results/create-delete-local-01
```

上記は**現行コードの再測定**です。表の数値そのものを追試する場合は、次のように測定コミットを別の作業ツリーに展開し、そこで手順2を実行します。レポートに記載した profile・fixture・CPU 配置・測定条件を使用してください。

```bash
git fetch origin e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614
git worktree add --detach ../example-rails-aot-pilot e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614
cd ../example-rails-aot-pilot
```

hosted runner の物理環境を完全には再現できないため、数値の一致は保証されません。より長い実験の設定、k6 による open-arrival 測定、GCE 移行時の注意点は[ベンチマーク手順](docs/benchmark.md)と[`bench/README.md`](bench/README.md)にあります。Roundhouse の処理の仕組みと、本リポジトリの測定で変わる範囲は[アーキテクチャ Appendix](docs/provenance.md#appendix-roundhouse-architecture-v2026918)を参照してください。

## ライセンス

このリポジトリの独自のコードと文書は [Apache License 2.0](LICENSE) で公開します。[同梱の Roundhouse 由来スクリプト](scripts/vendor/create-blog)は[別途 MIT License](scripts/vendor/LICENSE-MIT) です。Roundhouse、Spinel、Rails や生成物に含まれる第三者のソフトウェアには各自のライセンスが適用されます。
