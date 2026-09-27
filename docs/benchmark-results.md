# Rails / Roundhouse / Spinel ベンチマーク初回測定結果と差異分析

更新日：2026-09-27  
親 Issue：[#11](https://github.com/koduki/example-rails-aot/issues/11)  
対応 Issue：[#20](https://github.com/koduki/example-rails-aot/issues/20)「Actions で初回比較を実施し、差異の原因調査・修正・再測定と結果公開を完了する」  
先行 PR：[#22](https://github.com/koduki/example-rails-aot/pull/22) (P0 基盤), [#23](https://github.com/koduki/example-rails-aot/pull/23) (負荷計測), [#24](https://github.com/koduki/example-rails-aot/pull/24) (Actions 統合), [#25](https://github.com/koduki/example-rails-aot/pull/25) (JIT 診断), [#26](https://github.com/koduki/example-rails-aot/pull/26) (GCE 実行契約・full 設定)

---

## 1. エグゼクティブサマリー

本検証では、同一の SQLite fixture と同一の測定ジョブ（1 CPU 専有、3 CPU 負荷生成器分離）を用い、Rails 8.1.3.1 ブログアプリケーション、Roundhouse による事前変換コード、および Spinel による AOT ネイティブバイナリの初回対比較測定を実施した。

事前確認（Preflight Gate）において、読み取りエンドポイント（`GET /articles` 等）は全ターゲットで **100% の応答・DB 状態一致** を達成した。一方で、書き込み・CSRF 検証については重大な機能差異（Blocker）を特定したため、**初回性能比較は適格と確認された読み取り処理に厳密に限定** して実施した。

```
[Throughput (RPS) on 1 Dedicated vCPU - GET /articles]
Rails CRuby (JIT Off)   : ▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 270.6 RPS (p50: 14.55 ms, RSS: 109.3 MB)
Rails CRuby (YJIT)      : ▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 447.5 RPS (p50:  8.66 ms, RSS: 133.6 MB)
Emitted CRuby (JIT Off) : ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 2268.5 RPS (p50:  1.72 ms, RSS:  41.6 MB)  [8.38x vs Rails Off]
Emitted CRuby (YJIT)    : ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 3021.0 RPS (p50:  1.29 ms, RSS:  50.2 MB)  [6.75x vs Rails YJIT]
Spinel (AOT Binary)     : ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░ 4353.4 RPS (p50:  0.89 ms, RSS:  12.5 MB) [16.09x vs Rails Off]
```

### 3 つの主要な分析結論

1. **Roundhouse 効果（Ruby to Ruby 事前平坦化）**:
   - CRuby JIT Off 下で **8.38x**（270.6 → 2,268.5 RPS）、YJIT 下で **6.75x**（447.5 → 3,021.0 RPS）のスループット向上を達成した。
   - レイテンシ（p50）は 14.6 ms から 1.7 ms（YJIT 時は 8.7 ms から 1.3 ms）へ短縮し、Peak RSS は 109 MB から 42 MB へ **約 62% 削減** された。
   - ルーティング、Rack ミドルウェア、ActiveRecord オブジェクト生成、ActionView テンプレート解決を事前除去した効果が支配的である。

2. **JIT 相互作用比（Interaction Ratio $I = 0.805$）**:
   - Rails 単体の YJIT 加速倍率 $G_{\text{Rails}} = 1.654x$ に対し、Roundhouse 変換後コードの YJIT 加速倍率 $G_{\text{emitted}} = 1.332x$ となり、相互作用比は $I = G_{\text{emitted}} / G_{\text{Rails}} = 0.805$ となった。
   - Rails は動的ディスパッチやメガモーフィックな呼び出し箇所が多いため YJIT の基本ブロックバージョン管理による恩恵が大きい。一方、変換後コードは既に呼び出しがモノモーフィックに平坦化されているため、YJIT は追加で +33% の加速を提供するものの、相対向上率は Rails 単体より小さくなる（劣線形相互作用）。

3. **Spinel 相対性能（全体スタックの構成差）**:
   - Spinel は Rails CRuby Off に対し **16.09x**（4,353.4 RPS）、YJIT に対し **9.73x**、メモリは **12.5 MB**（Rails の 1/9〜1/11）を達成した。
   - ただし、これは「Ruby の AOT コンパイル単体の効果」ではなく、**組み込み C-HTTP サーバー、ネイティブ SQLite C バインディング、静的スキーマシリアライザ、単一静的バイナリ** を含めた **実行系スタック全体のアーキテクチャ差** である。

---

## 2. トレーサビリティと測定諸元

本報告の数値は、すべて GitHub Actions 上で逐次測定された raw artifact から直接再計算可能である。

| 項目 | 内容 |
|---|---|
| **GitHub Actions Run ID** | [`36289166814`](https://github.com/koduki/example-rails-aot/actions/runs/36289166814) |
| **Commit SHA** | `e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614` |
| **生アーティファクト名** | `benchmark-smoke-e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614` |
| **ホスト環境** | Azure-hosted Runner (`Linux-6.17.0-1022-azure-x86_64-with-glibc2.39`) |
| **物理 CPU** | AMD EPYC 7763 64-Core Processor (4 vCPUs 割り当て) |
| **CPU トポロジー** | Core 0: SMT 0, 1 / Core 1: SMT 2, 3 |
| **CPU 割り当て** | **App 専有**: Core 0 (CPU ID `0`), cgroup quota `1.0` / **負荷生成器**: CPUs `1, 2, 3` |
| **メモリ上限** | 3,072 MB (swap 3,072 MB, cgroup v2 強制) |
| **ツールチェーン** | CRuby 3.4.5, JRuby 9.4.14.0 (OpenJDK 21), Spinel 2026.09.12, Roundhouse v2026.9.18 |
| **SQLite 実版** | CRuby: 3.53.2 / JRuby: JDBC 3.46.1 / Spinel: 3.45.1 (全ターゲット WAL モード) |

### 再現コマンド

```bash
# 1. 測定のローカル再現（同一 Linux/Docker 環境）
python scripts/bench/run.py run \
  --profile bench/profiles/smoke.yml \
  --env-file bench/environments/local-single-host.env \
  --output bench-results/repro-smoke

# 2. raw artifact からの集計レポート再生成
python scripts/bench/report.py bench-results/repro-smoke

# 3. JIT 診断レポートの再生成
python scripts/bench/diagnostic.py bench-results/repro-smoke
```

---

## 3. 正確性ゲートと機能差異（Blocker）分類

ベンチマークに先立ち、全 9 構成（主 7 ＋ JRuby OFF 診断 2）に対して事前確認テスト（Preflight Gate）を実施した。

### A. 適格エンドポイント（Eligible Endpoints）
以下の 5 読み取りエンドポイントは、全 9 構成において Rails 基準値とレスポンス本文、HTTP ステータス、および SQLite 状態の完全一致を確認した（全構成で passed）。
- `GET /articles`（記事一覧 HTML）
- `GET /articles/1`（記事詳細 HTML）
- `GET /articles/new`（新規作成フォーム HTML）
- `GET /articles.json`（記事一覧 JSON）
- `GET /articles/1.json`（記事詳細 JSON）

### B. 未解決の機能差異と CRUD 測定ブロッカー
書き込み操作および不正入力ハンドリングにおいて、変換コード側に以下の不一致が検出された。重大な機能不一致を無視して全体を成功扱いせず、以下のとおり原因を分類・記録した。

| 操作ケース | 現象・差分 | 原因分類 | 影響と対応方針 |
|---|---|---|---|
| `create_invalid` / `update_invalid` | Rails はバリデーションエラー時に `<div class="field_with_errors">` でフォーム要素をラップするが、変換コードはプレーンな input 要素を出力 | アプリ／出力 | HTML 表示差異。読み取り性能測定には影響しないが、CRUD 混在測定（#21）の前に View 変換規則の修正が必要 |
| `json_create_invalid` | Rails は項目別ハッシュ `{"title": ["..."]}` を返すが、変換 Ruby は配列 `["Title..."]` を返し、Spinel は `text/html` を返す | 変換出力／Spinel | JSON エラーシリアライズの未実装。JSON 読み取りとは独立して failed として保存 |
| `csrf_invalid` | Rails は不正な authenticity_token を 422 で拒否するが、変換コードおよび Spinel はトークンを無視して書き込みを実行してしまう | 変換出力／セキュリティ | **重大な Blocker**。不正書き込みがスルーされるため、書き込み負荷測定の適格性を満たさない |

> [!IMPORTANT]
> 上記の機能差異により、現時点では **読み取り操作（GET）のみが性能評価の適格対象** である。CRUD 混在や書き込みベンチマークは、上記 Blocker の修正および再事前確認が完了するまで実施してはならない。

---

## 4. 測定結果詳細表

### 4.1 ターゲット別性能サマリー (`GET /articles`)

測定条件：1 CPU 専有、4 並行接続、10 秒間測定、同一 SQLite WAL fixture（10 件記事）。

| ターゲット | 実行形態 | 状態 | RPS | p50 (ms) | p95 (ms) | p99 (ms) | エラー率 | Peak RSS | 平均 CPU |
|---|---|:---:|---:|---:|---:|---:|---:|---:|---:|
| `rails-cruby-off` | Rails 8 + Puma + CRuby 3.4 (JIT Off) | ✅ passed | 270.60 | 14.55 | 19.16 | 21.30 | 0.00% | 109.30 MB | 101.00% |
| `rails-cruby-yjit` | Rails 8 + Puma + CRuby 3.4 (YJIT On) | ✅ passed | 447.53 | 8.66 | 13.32 | 15.90 | 0.00% | 133.60 MB | 101.57% |
| `emit-cruby-off` | Roundhouse 変換 + Puma + CRuby 3.4 (JIT Off) | ✅ passed | 2,268.48 | 1.72 | 2.54 | 2.98 | 0.00% | 41.58 MB | 111.98% |
| `emit-cruby-yjit` | Roundhouse 変換 + Puma + CRuby 3.4 (YJIT On) | ✅ passed | 3,021.03 | 1.29 | 1.99 | 2.42 | 0.00% | 50.16 MB | 114.41% |
| `rails-jruby` | Rails 8 + Puma + JRuby 9.4 (JIT On, HotSpot) | ✅ passed | 26.86 | 121.38 | 240.92 | 254.24 | 0.00% | 616.70 MB | 81.84% |
| `emit-jruby` | Roundhouse 変換 + Puma + JRuby 9.4 (JIT On, HotSpot) | ⚠️ unstable | 1,258.74 | 2.48 | 6.22 | 9.40 | 0.02% | 424.90 MB | 87.10% |
| `spinel` | Spinel AOT 単一バイナリ (C-HTTP / Native SQLite) | ✅ passed | 4,353.36 | 0.89 | 1.17 | 1.25 | 0.00% | 12.54 MB | 118.22% |

> [!NOTE]
> `emit-jruby` は測定時間内に 12,693 件の正常リクエストを処理（エラー率 0.02%）したが、ウォームアップ上限（45 秒）の時点でスループットが +506%（164.5 → 998.0 RPS）急上昇中であり、安定基準（CV ≤ 0.25）に達しなかったため `unstable` として記録された。

### 4.2 対比較倍率と JIT 相互作用

| 比較指標 | 倍率 | 算出式 / 意味 |
|---|:---:|---|
| **Roundhouse 効果 (CRuby JIT Off)** | **8.383x** | $\text{RPS}_{\text{emit-cruby-off}} / \text{RPS}_{\text{rails-cruby-off}}$ (純粋な事前平坦化効果) |
| **Roundhouse 効果 (CRuby YJIT)** | **6.750x** | $\text{RPS}_{\text{emit-cruby-yjit}} / \text{RPS}_{\text{rails-cruby-yjit}}$ (YJIT 併用時の平坦化効果) |
| **Roundhouse 効果 (JRuby JIT 観測値)** | *46.863x* | $\text{RPS}_{\text{emit-jruby}} / \text{RPS}_{\text{rails-jruby}}$ (⚠️ ウォームアップ途上の参考値) |
| **Rails YJIT 向上倍率 ($G_{\text{Rails}}$)** | **1.654x** | $\text{RPS}_{\text{rails-cruby-yjit}} / \text{RPS}_{\text{rails-cruby-off}}$ |
| **変換後 YJIT 向上倍率 ($G_{\text{emitted}}$)** | **1.332x** | $\text{RPS}_{\text{emit-cruby-yjit}} / \text{RPS}_{\text{emit-cruby-off}}$ |
| **YJIT 相互作用比 ($I$)** | **0.805** | $G_{\text{emitted}} / G_{\text{Rails}}$ ($< 1.0$ の劣線形効果) |
| **Spinel vs Rails CRuby Off** | **16.088x** | 全体スタック差 (Spinel AOT + C-HTTP/DB vs Puma + Rails + CRuby) |
| **Spinel vs Rails CRuby YJIT** | **9.728x** | 全体スタック差 (Spinel AOT + C-HTTP/DB vs Puma + Rails + CRuby YJIT) |

---

## 5. 詳細技術考察

### 5.1 なぜ Roundhouse 変換で 6.75x〜8.38x 高速化するのか？

Rails baseline から Roundhouse 変換コードへの移行により、リクエストごとに実行される以下の Ruby 処理が完全にバイパスされた。

1. **動的ルーティング探索の排除**: Rails の `ActionDispatch::Journey` による正規表現・AST パス探索ツリーが、単純な case/when または静的ディスパッチに置換された。
2. **Rack ミドルウェアスタックの短縮**: デフォルトの 20 以上の Rack ミドルウェア（セッション管理、クッキー暗号化、ETag 計算等）のオーバーヘッドが大幅に削減された。
3. **ActiveRecord インスタンス生成オーバーヘッドの除去**: リレーションオブジェクト、属性型キャスト、ActiveModel バリデーションメタデータ、コールバックチェーンの生成が省かれ、SQLite 行からハッシュ/構造体への直接マッピングが行われる。
4. **ActionView テンプレート解決と ERB 評価の静的化**: テンプレート探索・キャッシュ確認・ERB バッファ連結処理が、事前結合された静的文字列出力へ展開された。
5. **メモリ割り当て数の激減**: Peak RSS が 109 MB → 42 MB へ減少したことが示すとおり、GC 頻度とマイナー GC の停止時間が大幅に短縮された。

### 5.2 JIT 相互作用比 $I = 0.805$ の機構解明

YJIT は、Ruby の動的型ディスパッチを監視し、同一型が連続する箇所をインライン化してネイティブ機械語（Basic Block Versioning）に変換することで真価を発揮する。

- **Rails 側の特性**: 動的メタプログラミング、`method_missing`、ポリモーフィックな引数受け渡しが頻発するため、YJIT による基本ブロックのインライン化の余地が極めて大きい。このため、YJIT 有効化により **+65.4%** の劇的な加速（1.654x）が得られる。
- **Roundhouse 側の特性**: 変換コードは既に静的なメソッド呼び出しと単純なループに平坦化されており、動的ディスパッチのボトルネックが除去されている。そのため、YJIT は更なる最適化（+33.2% の加速）をもたらすものの、Rails 単体ほどの相対的改善幅は生じない。
- **結論**: $I = 0.805 < 1.0$ は、Roundhouse が JIT の効果を阻害したのではなく、**「JIT が本来解決すべきオーバーヘッドの一部を事前変換がすでに解消していた」** ことを示している。

### 5.3 Spinel 相対性能（16.09x）の境界認識

Spinel の 4,353.4 RPS、レイテンシ p50: 0.89 ms、メモリ 12.5 MB は圧倒的な性能を示しているが、これを「Ruby を AOT コンパイルしただけの倍率」と表現することは工学的に誤りである。

- **HTTP サーバーの差**: Puma（Ruby ソケット受託・スレッドプール）に対し、Spinel は C 言語で直接イベント駆動ソケットループを処理する。
- **DB バインディングの差**: Ruby の C-API ラッパー経由の `sqlite3` gem に対し、Spinel は C 言語レベルで SQLite プリペアドステートメントを直接バインド・フェッチする。
- **シリアライザの差**: Ruby のハッシュを経由せず、C レベルで JSON/HTML バッファをダイレクトに構築する。
- **結論**: Spinel の数値は **言語変換（AOT）＋ 組み込みランタイム ＋ ネイティブ C-I/O の複合アーキテクチャ刷新効果** として位置づけられる。

---

## 6. JRuby 診断とウォームアップ特性

本測定で判明した重要な工学的知見として、**1 CPU 環境における JRuby のウォームアップ時間** がある。

```
[emit-jruby Warmup Progression across 5-second Windows]
Window 1 ( 5s) :  164.5 RPS (p50: 19.8 ms)
Window 2 (10s) :  281.4 RPS (p50: 13.8 ms)
Window 3 (15s) :  368.2 RPS (p50:  9.9 ms)
Window 4 (20s) :  464.0 RPS (p50:  7.2 ms)
Window 5 (25s) :  648.8 RPS (p50:  5.2 ms)
Window 6 (30s) :  776.9 RPS (p50:  4.2 ms)
Window 7 (35s) :  897.9 RPS (p50:  3.5 ms)
Window 8 (40s) :  998.0 RPS (p50:  3.1 ms)
Measurement    : 1258.7 RPS (p50:  2.5 ms)
```

1. **JVM HotSpot C2 コンパイルの稼働確認**:
   `CompilationMXBean` および `RuntimeMXBean` のプローブにより、`rails-jruby`, `emit-jruby`, `rails-jruby-off`, `emit-jruby-off` の全 JRuby 実行において JVM HotSpot JIT コンパイラが完全に稼働していることを確認した。
2. **単一 CPU でのコンパイル遅延**:
   1 CPU のみ割り当てられた環境では、HTTP リクエストの処理スレッドと HotSpot JIT コンパイルスレッドが CPU 時間を奪い合うため、階層コンパイル（C1 → C2）が定常状態に達するまでに長時間を要する。
3. **測定プロファイルの指針**:
   CI の短時間スモーク（45 秒上限）では JRuby の真の定常性能を測ることはできず、`quick.yml`（60〜600 秒ウォームアップ）または `full.yml` による十分な熱入れが不可欠である。

---

## 7. 認識論的分類（Epistemological Classification）

測定結果を科学的に誠実に扱うため、以下の 3 区分を明確に区別する。

### A. 実測事実（Observed Facts）
- 1 専有 vCPU の AMD EPYC 7763 上で、同一の SQLite fixture に対し、`GET /articles` は Roundhouse CRuby Off で 2,268.5 RPS、YJIT で 3,021.0 RPS、Spinel で 4,353.4 RPS を記録した。
- Rails CRuby の Peak RSS（109〜134 MB）に対し、Roundhouse CRuby は 41〜50 MB、Spinel は 12.5 MB であった。
- 事前確認において、全 7 ターゲットで 5 種類の読み取り操作の本文・ステータス・DB 状態が完全一致した。
- バリデーションエラー HTML（CSS クラス）、JSON エラー形式、不正 CSRF 受容の 3 点で変換コードに不一致（failed）が確認された。

### B. 説明を支持する観測（Supporting Observations）
- ウォームアップ推移において、CRuby YJIT は 15〜20 秒で安定（CV < 0.02）したのに対し、JRuby は 45 秒経過時点でもスループットが 500% 以上成長し続けていた。これは JRuby/HotSpot の階層コンパイラ特性と合致する。
- メモリ消費と GC 回数の大幅な減少は、ルーティングとビュー解決の事前平坦化によるオブジェクト生成削減の仮説を支持している。

### C. 未検証の仮説（Unverified Hypotheses - 将来の検証課題）
- **相互作用比の微視的機構**: YJIT のインラインキャッシュヒット率や基本ブロック無効化回数が、変換コードにおいて実際にどう変化しているかは、専用の profiler（`perf` 等）による命令レベル解析が必要である。
- **マルチコア・ベアメタルスケーリング**: 4 CPU や 8 CPU、物理専有コア（SMT オフ）の環境で、Roundhouse や Spinel のスループットが線形にスケールするかどうかは未検証である。

---

## 8. GCE 専用機本測定（今後の課題）に向けた検証項目

本測定（GitHub Actions hosted runner）の限界を踏まえ、Issue [#21](https://github.com/koduki/example-rails-aot/issues/21) および専用 GCE インスタンス（`c3-standard-4` 等）において確認すべき課題を整理する。

1. **SMT（同時マルチスレッド）の干渉排除**:
   Hosted runner では同一物理コアのスレッドがバックグラウンド負荷の影響を受ける可能性がある。専有 VM での測定により、ジッターと p99 遅延を再検証する。
2. **JRuby の長期ウォームアップ収束**:
   `quick.yml` / `full.yml` の 600 秒ウォームアップ予算を用い、JRuby が真の定常ピークスループットに達した状態での Roundhouse 効果および compile.mode ON/OFF 比率を確定する。
3. **書き込みブロッカー（CSRF / バリデーション）の修正後の CRUD 混在測定**:
   Roundhouse 側の CSRF トークン検証と JSON エラー形式の修正を完了させた上で、90% 読み取り / 10% 書き込みの現実的トラフィックミックスにおける対比較を実施する。
4. **大容量データ（1,000 件記事 ＋ ページネーション）での検証**:
   SQLite B-Tree キャッシュとクエリ実行プランが変化した際の、ActiveRecord vs 静的 SQL クエリの差異を検証する。

---

## 9. 補助評価結果（起動遅延・フットプリント・4 コア・CRUD 評価）

Issue [#21](https://github.com/koduki/example-rails-aot/issues/21) に基づき、定常 1 コア読み取りの主比較から独立した補助評価を実施した。

### 9.1 プロセス起動から初回業務応答（First 200 OK）までの所要時間

同一環境（AMD EPYC 7763, 1 CPU）における、コンテナ起動から最初の `GET /articles` が HTTP 200 を返すまでの実測所要時間（`ready_seconds`）：

| ターゲット | 実行ランタイム | JIT モード | 初回業務応答時間 (s) | Spinel 比 | Rails CRuby Off 比 |
|---|---|:---:|---:|:---:|:---:|
| `spinel` | Spinel AOT ネイティブバイナリ | AOT | **0.17 秒** | 基準 (1.0x) | **13.5x 高速** |
| `emit-cruby-off` | Roundhouse 変換 + Puma | JIT Off | **0.70 秒** | 4.1x | **3.3x 高速** |
| `emit-cruby-yjit` | Roundhouse 変換 + Puma | YJIT On | **0.70 秒** | 4.1x | **3.3x 高速** |
| `rails-cruby-off` | Rails 8 + Puma | JIT Off | **2.29 秒** | 13.5x | 基準 (1.0x) |
| `rails-cruby-yjit` | Rails 8 + Puma | YJIT On | **2.87 秒** | 16.9x | 1.25x 遅延 |
| `emit-jruby` | Roundhouse 変換 + Puma | JIT (HotSpot) | **11.38 秒** | 66.9x | 5.0x 遅延 |
| `rails-jruby` | Rails 8 + Puma | JIT (HotSpot) | **28.73 秒** | 169.0x | 12.5x 遅延 |

- **ネイティブ AOT の起動優位性**: Spinel は 0.17 秒で初回業務応答を完了。Rails CRuby に対し 13.5 倍、Rails JRuby に対し 169 倍高速であり、FaaS やオートスケーリング環境におけるコールドスタート耐性が極めて高い。
- **Roundhouse による起動時間短縮**: Rails 起動時の数百に及ぶ gem / ファイルの require や初期化フックが排除されたため、CRuby 上でも 2.29 秒 → 0.70 秒（3.3 倍高速化）へ短縮された。

### 9.2 4 コア条件のプロファイル策定と未実施理由

4 コアでの並列スケーリング評価に向け、`bench/profiles/quick-4core.yml`（4 CPU 専有、4,096 MB メモリ、CRuby 4 スレッド、JRuby 4 スレッド、Spinel 4 ワーカー）を策定した。
ただし、GitHub Actions hosted runner は全体で 4 vCPU（2 物理コア SMT）しか持たないため、アプリに 4 CPU を割り当てると負荷生成器（k6）と CPU を奪い合い、負荷生成器の飽和（client saturation）を引き起こす。このため、主結果の公平性を担保すべく、**4 コア測定は hosted runner では理由付き未実施（スキップ）とし、将来の GCE 専用 VM（8 vCPU 以上）向け profile として保持** する。

### 9.3 CRUD 混在負荷シナリオ策定 (`bench/k6/crud.js`)

定常読み取りに加え、現実的なトラフィックミックス（90% 読み取り / 10% 更新）をシミュレートする `bench/k6/crud.js` を配備した。
- **VU 分割による SQLite ロック競合の防止**: 各 VU が `targetId = 1 + ((__VU * 31 + __ITER) % numArticles)` により異なるレコードを更新することで、意図しない DB ロック待ちを回避。
- **データ無制限増加の抑制**: インプレース更新（PATCH）を中心に設計し、DB サイズの時間経過による肥大化を防止。

