# Rails / Roundhouse / Spinel 初回予備測定

更新日：2026-09-27  
親 Issue：[#11](https://github.com/koduki/example-rails-aot/issues/11)  
対象：[#20](https://github.com/koduki/example-rails-aot/issues/20)、[#21](https://github.com/koduki/example-rails-aot/issues/21)

## 測定の位置付け

初回測定は GitHub Actions の hosted runner で行った **closed-loop の smoke 測定**である。各ターゲットは `GET /articles` を4接続で10秒間、1回ずつ測定した。負荷生成には `scripts/bench/driver.py` を使用した。k6 の open arrival 計測、容量探索、専用 GCE ホストでの反復測定はまだ実施していない。

この測定からは短時間の処理量と応答時間の観測値を読み取れる。定常状態の最大容量、一般化できる倍率、JIT の内部機構は確定できない。特に JRuby はウォームアップが収束しなかった。以下の倍率はこの試行に限る参考値であり、本測定の結論として扱わない。

## 出典と条件

| 項目 | 記録・条件 |
| --- | --- |
| [Actions 実行](https://github.com/koduki/example-rails-aot/actions/runs/36289166814) | `36289166814` |
| 測定コミット | `e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614` |
| 生データ | `benchmark-smoke-e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614` の `measurement/plan.json`、`measurement/trials/per-run.json` |
| 測定用 Rails / Ruby | Rails **8.0.5.1**、CRuby **3.4.5**、JRuby **10.0.7.0**。元の `blog/` は Rails 8.1.3.1 |
| fixture | 全試行で同じ内容を新規生成。SQLite WAL、**3記事と3コメント** |
| 配置 | アプリは論理 CPU 0、負荷生成は論理 CPU 1〜3。CPU 0 と 1 は同じ物理コアの SMT スレッド |
| メモリ | `docker stats` のコンテナメモリ使用量。プロセス RSS ではない |
| 反復 | 各ターゲット1回、各10秒。`allow_unstable` を有効化した smoke profile |

同一 fixture、CPU 指定、実行ジョブを揃えているが、hosted runner の物理コアや他のホスト負荷を専有した測定ではない。各値は生データから再計算できる。既存 artifact の保存期限を過ぎた場合は同じ commit と profile から再実行する。

## 正確性ゲート

**後続の検証方針（この予備測定には適用しない）：** ベンチマーク専用の Rails
設定では CSRF 検証を無効化し、Roundhouse 出力と条件を合わせる。CRUD の
`mix` シナリオは読み取りと正常な更新だけを測定し、その操作の preflight
一致を必須とする。無効入力の HTML/JSON と不正 CSRF の差は引き続き記録し、
この限定した測定からアプリケーション全体の同等性や安全性を主張しない。
手順と範囲は [ベンチマーク手順](benchmark.md#benchmark-only-crud-scope) を参照。

測定前の preflight では、9構成について5種類の GET の HTTP 応答と DB 状態を Rails 基準と比較し、これらの読み取りは適格と判定した。初回性能測定の対象は7構成の `GET /articles` のみである。

初回測定時、無効な create/update の HTML、JSON バリデーションエラー形式、不正 CSRF トークンでの書き込み拒否が一致せず、CRUD 性能測定を遮断した。その後の改修で正常系の操作だけを測る限定シナリオを追加した。現在の機能検証状況は[手順書](benchmark.md#benchmark-only-crud-scope)を参照。初回測定の数値や正確性判定を後続の実験に流用しない。

## 観測値

| 構成 | 状態 | RPS | p50 ms | p95 ms | p99 ms | コンテナメモリ最大 MB | 平均 CPU % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rails / CRuby JIT Off | passed | 270.60 | 14.55 | 19.16 | 21.30 | 109.30 | 101.00 |
| Rails / CRuby YJIT | passed | 447.53 | 8.66 | 13.32 | 15.90 | 133.60 | 101.57 |
| Roundhouse / CRuby JIT Off | passed | 2,268.48 | 1.72 | 2.54 | 2.98 | 41.58 | 111.98 |
| Roundhouse / CRuby YJIT | passed | 3,021.03 | 1.29 | 1.99 | 2.42 | 50.16 | 114.41 |
| Rails / JRuby JIT | passed | 26.86 | 121.38 | 240.92 | 254.24 | 616.70 | 81.84 |
| Roundhouse / JRuby JIT | **unstable** | 1,258.74 | 2.48 | 6.22 | 9.40 | 424.90 | 87.10 |
| Spinel AOT | passed | 4,353.36 | 0.89 | 1.17 | 1.25 | 12.54 | 118.22 |

この closed-loop 測定内では Roundhouse / Rails の処理量比が CRuby Off で **8.383**、YJIT で **6.750**、Rails 内の YJIT 比が **1.654**、Roundhouse 内が **1.332**、その比が **0.805** だった。Spinel / Rails CRuby Off は **16.088** だった。いずれも単一の短い試行に由来し、容量倍率の推定値ではない。Spinel との比較はサーバーと DB アダプターを含む実行系全体の比較である。

Roundhouse / JRuby JIT は45秒のウォームアップ上限で収束せず、実行中も処理量が増加した。表の値および Rails JRuby との比は順位付けから除外する。YJIT や JRuby JIT がどの内部処理を改善したか、GC 削減にどの要素が寄与したかは、診断ログと再測定で検証する仮説である。

## 次の測定

1. 修正後の k6 経路で offered RPS、drop、応答時間、エラーを確認する。固定 offered RPS での比較は同じ負荷に対する遅延・資源・エラーの比較とする。最大容量と倍率には複数の負荷水準で SLO を満たす上限を探索する必要がある。
2. 専用ホストでは `full.yml` を使用し、ウォームアップと反復を実行する。175試行のウォームアップ最小値と測定時間の合計は14時間35分である。hosted Actions の選択肢から `full` を外してある。
3. 書き込み差異の修正と preflight の通過後に CRUD を実施する。4 CPU 比較には CRuby Puma の複数ワーカーを使い、配置・負荷生成余力を記録する。`prepare.py` の `fetch_page` は fixture を調べる補助関数であり、HTTP アプリのページネーションではない。

### 再生成

```sh
python3 scripts/bench/report.py bench-results/measurement
python3 scripts/bench/diagnostic.py bench-results/measurement
```
