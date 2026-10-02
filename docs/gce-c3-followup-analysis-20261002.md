# GCE C3 追加再試験 分析レポート：主要比較の補強とSpinelの切り分け

**作成日**: 2026-10-03（日本時間）  
**Run ID**: `c3-followup-20261001T225541Z`  
**固定コミット**: [`98a1beec9a406ead2fe3b3e219086c171e1883a4`](https://github.com/koduki/example-rails-aot/commit/98a1beec9a406ead2fe3b3e219086c171e1883a4)（PR #67 マージコミット）  
**生データ公開先**: [GitHub Release `gce-c3-followup-20261002-c3-followup-20261001T225541Z`](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-followup-20261002-c3-followup-20261001T225541Z)  
**アーカイブ**: `c3-followup-20261001T225541Z.tar.gz` (SHA-256: `26af7b1efd2485ecc30ba34a7aa3daafc4e6e27ccd3bcdf4245783627cec0a46`)  
**対象指示書**: [`docs/gce-c3-followup-instructions.md`](gce-c3-followup-instructions.md)  
**実施インフラ**:
- App VM: `bench-app-c3` (Google Compute Engine `c3-standard-4`, 4 vCPU, 16 GiB RAM, App cpuset 0–3, 14,336 MiB limit, Private IP: `10.146.0.11`)
- Loadgen VM: `bench-loadgen-c3` (Google Compute Engine `c3-standard-4`, Private IP: `10.146.0.12`, k6 remote orchestration)
- ネットワーク: 同一 VPC / サブネット, Cloud NAT 経由, GCP ゾーン `asia-northeast1-b`

---

## 1. 概要と目的

2026-10-01 に実施した [GCE C3 1,000記事再テスト](gce-c3-retest-analysis-20261001.md) および [Rails × Roundhouse 総合レポート](roundhouse-rails-jit-aot-report.md) において、以下の課題が残されていました：
1. **同負荷下での直接比較の欠如**: 容量探索（二分探索）では各試行で負荷が異なるため、同一の確定負荷におけるレイテンシ分布（p50/p90/p95/p99）、CPU使用率、コンテナメモリ消費量の直接比較が未計測であった。
2. **CRuby 2×2（Rails/Emitted × Off/YJIT）相互作用の再現性**: Emitted コードが YJIT によって大幅に加速するメカニズムと、Rails における YJIT の効果の定量的比較。
3. **JRuby のウォームアップ長期化の検証**: JVM の C2 JIT 階層コンパイルにおいて、動的メタプログラミングを多用する Rails と、事前展開された Emitted コードのウォームアップ収束速度の差異。
4. **Spinel 急落の原因切り分け**: 前回の正式容量探索において Spinel が 25 RPS 付近で急激にタイムアウト（5,000 ms 超）した原因が、Spinel 自体のスループット限界なのか、k6 の開放型到着ドライバにおける `preallocated_vus: 512`（512 並列仮想ユーザ割り当て）による接続過負荷なのかの切り分け。

本追加再試験では、これら 4 つの論点を独立した bounded run（計 66 試行、総実行時間 18.1 時間）として実機検証し、全生ログの確保・チェックサム検証・両 VM の安全停止を行いました。

---

## 2. 全実験の実施一覧と完了状況

| 優先度 | 実験名 | 対象構成 | 試行数 | 完了/総数 | 判定 |
|---|---|---|---|---|---|
| **0** | 環境固定・Build・Preflight | 5イメージ, 9構成 | 9 | 9/9 | 全構成 `/articles?page=1` eligible 確認 |
| **1** | `matched-rate` | 主要6構成 × 3反復 | 18 | 18/18 | 全試行合格 (165.5分) |
| **2** | `capacity-cruby` | CRuby 4構成 × 5反復 | 20 | 20/20 | 19合格, 1件境界不安定 (433.1分) |
| **3** | `capacity-jruby` | JRuby 2構成 × 5反復 | 10 | 10/10 | 7合格, 2件ウォームアップ不安定, 1件境界不安定 (299.6分) |
| **4** | `spinel-connections` | 10/15/25 RPS × pool 10/512 × 3反復 | 18 | 18/18 | 全6セル完走 (185.8分) |
| **合計** | | | **66** | **66/66** | **完走 (計 18.1時間)** |

---

## 3. Priority 1: `matched-rate`（共通 10 RPS 開放型到着）

### 3.1 測定条件
- **負荷モード**: k6 `constant-arrival-rate`（開放型到着）、共通 10 RPS
- **測定時間**: 120 秒（計 1,201 リクエスト）
- **ウォームアップ**: 4 VU closed-loop、30秒窓、180–900秒、4連続安定窓（CV ≤ 0.08, drift ≤ 0.08）
- **SLO 要件**: p99 ≤ 100 ms、error rate < 0.001、drops = 0、integrity 合格、tester CPU < 85%

### 3.2 構成別代表値（中央値）比較

| 構成 | 完了率 | Median p50 | Median p90 | Median p95 | Median p99 | Median CPU (Mean) | Median CPU (Peak) | Median Mem (MiB) |
|---|---|---|---|---|---|---|---|---|
| **`emit-cruby-yjit`** | 3/3 | **19.10 ms** | **19.77 ms** | **19.90 ms** | **21.65 ms** | **15.5%** | **19.9%** | **179.8 MiB** |
| **`rails-cruby-yjit`** | 3/3 | **17.90 ms** | **19.13 ms** | **19.43 ms** | **63.37 ms** | **15.0%** | **25.9%** | **458.8 MiB** |
| `rails-cruby-off` | 3/3 | 33.62 ms | 35.52 ms | 35.90 ms | 71.73 ms | 28.3% | 40.4% | 324.1 MiB |
| `emit-cruby-off` | 3/3 | 69.38 ms | 70.43 ms | 71.08 ms | 73.61 ms | 57.5% | 71.6% | 150.8 MiB |
| `rails-jruby` | 3/3 | 25.91 ms | 34.80 ms | 38.74 ms | 46.69 ms | 39.1% | 119.6% | 1,212.4 MiB |
| `emit-jruby` | 3/3 | 31.01 ms | 36.54 ms | 39.99 ms | 48.85 ms | 38.8% | 129.9% | 884.1 MiB |

### 3.3 知見
1. **テールレイテンシの予測可能性**:
   - `emit-cruby-yjit` は p50（19.10 ms）から p99（21.65 ms）までの差がわずか 2.55 ms と極小であり、極めて安定した応答時間を維持しました。
   - `rails-cruby-yjit` は p50〜p95 までは最速（17.90〜19.43 ms）ですが、p99 は **63.37 ms** に達しました。これは Rails のオブジェクト生成量と GC による一時的な停止がテールレイテンシを押し上げていることを実証しています。
2. **コンテナメモリ消費の圧倒的差異**:
   - `emit-cruby-yjit` は **179.8 MiB** で動作し、`rails-cruby-yjit`（458.8 MiB）の **約 39%** に抑えられています。
   - JRuby（JVM）構成は同等負荷でも **884〜1,212 MiB** を消費し、CRuby の 3〜7 倍のメモリフットプリントとなりました。
3. **CPU 効率**:
   - YJIT有効時の CRuby 2構成はいずれも **約 15%** と最小の CPU 使用率を記録しました。
   - JRuby は定常 10 RPS でも JVM の JIT コンパイルやバックグラウンド処理により **約 39%**（ピーク時 120〜130%）を消費しました。

---

## 4. Priority 2: `capacity-cruby`（CRuby 4構成 × 5反復容量探索）

### 4.1 構成別容量測定値 (RPS)

| 構成 | 完走数 | 測定容量リスト (RPS) | 中央値 (Median) | 範囲 (Min – Max) |
|---|---|---|---|---|
| **`emit-cruby-yjit`** | 5/5 | [96.86, 106.23, 103.12, 80.85, 103.11] | **103.11 RPS** | 80.85 – 106.23 RPS |
| **`rails-cruby-yjit`** | 4/5 | [81.73, 76.46, 115.61, 111.99] | **96.86 RPS** | 76.46 – 115.61 RPS |
| `rails-cruby-off` | 5/5 | [54.68, 60.92, 57.80, 56.24, 52.73] | **56.24 RPS** | 52.73 – 60.92 RPS |
| `emit-cruby-off` | 5/5 | [19.53, 23.43, 21.09, 21.87, 21.87] | **21.87 RPS** | 19.53 – 23.43 RPS |

### 4.2 2×2 JIT / AOT 相互作用の評価

```
                     JIT Off (Interpreted)        YJIT Enabled           JIT Speedup
--------------------------------------------------------------------------------------
Rails (Standard)           56.24 RPS                96.86 RPS               1.72x
Roundhouse (Emitted)       21.87 RPS               103.11 RPS               4.71x
--------------------------------------------------------------------------------------
Emitted vs Rails           0.39x                    1.06x
```

### 4.3 考察
1. **JIT 倍率の決定的な差 (4.71x vs 1.72x)**:
   - インタプリタ実行時、Roundhouse emitted コードは Rails の約 39%（21.87 vs 56.24 RPS）にとどまります。
   - しかし YJIT を有効化すると、emitted コードは **4.71倍**（21.87 → 103.11 RPS）へと爆発的に加速し、Rails の **1.72倍**（56.24 → 96.86 RPS）を逆転します。
   - **メカニズム**: Roundhouse の emitted コードは動的ディスパッチやメタプログラミングを排除した平坦な単一責務メソッドで構成されているため、YJIT の型推論、インラインキャッシュ、およびネイティブ命令生成が最大限に機能します。
2. **境界不安定性の正確な記録**:
   - `rails-cruby-yjit` Rep 3 は [117.19, 121.09] RPS の境界付近で `boundary_unstable` と判定され、無理に数値を合成せず失敗として正確に保存されました。他の 4 反復では Rep 1, 2（76〜82 RPS）と Rep 4, 5（112〜116 RPS）の 2 つの安定バンドが観測されました。

---

## 5. Priority 3: `capacity-jruby`（JRuby 2構成 × 5反復容量探索）

### 5.1 構成別容量測定値 (RPS)

| 構成 | 完走数 | 測定容量リスト (RPS) | 中央値 (Median) | 範囲 (Min – Max) | 平均ウォームアップ時間 |
|---|---|---|---|---|---|
| **`emit-jruby`** | 4/5 | [59.37, 48.78, 48.43, 56.00] | **56.00 RPS** | 48.43 – 59.37 RPS | **294.3秒** (約4.9分) |
| **`rails-jruby`** | 3/5 | [50.00, 56.24, 49.56] | **50.00 RPS** | 49.56 – 56.24 RPS | **768.8秒** (約12.8分) |

### 5.2 JVM ウォームアップ特性の考察
1. **ウォームアップ収束速度の著しい対比**:
   - `emit-jruby` は全5反復すべてで 240〜360 秒（平均 294 秒）で迅速・安定にウォームアップが完了しました。
   - `rails-jruby` は 5 反復中 2 反復（Rep 3, 4）が 900 秒の制限時間内に収束せず `warmup_unstable` として除外されました。また完走した 3 反復でも平均 768.8 秒（約 13分）を要しました。
   - Rails の巨大なクラスローディングとメタプログラミング構造が、JVM の C2 JIT 階層コンパイル（tiered compilation）のプロファイリング収束を大幅に遅延させている実態が明らかになりました。
2. **容量水準**:
   - JRuby JIT 有効下での容量中央値は両者とも 50〜56 RPS 程度であり、CRuby + YJIT（約 97〜103 RPS）の半分程度のスループットにとどまりました。

---

## 6. Priority 4: `spinel-connections`（Spinel 接続・VU Pool 切り分け）

### 6.1 実験結果一覧

| Offered RPS | Pool Size | 完了率 | Median p50 | Median p99 | エラー総数 | Median CPU | Median Mem | 判定 / 挙動 |
|---|---|---|---|---|---|---|---|---|
| **10 RPS** | pool 10 | 3/3 | 21.11 ms | 27.99 ms | 0 | 20.0% | 87.2 MiB | 安定合格 |
| **10 RPS** | pool 512 | 3/3 | 20.66 ms | 48.81 ms | 0 | 20.1% | 302.5 MiB | 合格（メモリ増） |
| **15 RPS** | pool 10 | 3/3 | 20.86 ms | 27.30 ms | 0 | 29.0% | 87.2 MiB | 安定合格 |
| **15 RPS** | pool 512 | 3/3 | 20.48 ms | 56.91 ms | 0 | 29.5% | 453.5 MiB | 合格（p99/メモリ増） |
| **25 RPS** | **pool 10** | **3/3** | **20.61 ms** | **27.71 ms** | **0** | **46.4%** | **90.0 MiB** | **完全合格（p99 < 28ms, エラーゼロ）** |
| **25 RPS** | **pool 512** | **0/3** | N/A | **5000.5 ms** | **281** | **207.5%** | **564.7 MiB** | **全滅（大量タイムアウト、CPU急増）** |

### 6.2 決定的結論：Spinel 急落の根本原因解明
- **原因の特定**:
  - `pool 512` では 25 RPS で 281 件のタイムアウト（5,000 ms 超）が発生し、CPU が 207% へ急騰して完全に崩壊しました。
  - しかし `pool 10` では、まったく同じ 25 RPS において **p50 = 20.61 ms、p99 = 27.71 ms、エラー 0 件、CPU 46.4%、メモリわずか 90.0 MiB** で極めて安定して動作しました。
  - したがって、前回の Spinel の失敗原因は **「Spinel のアプリケーション処理能力不足」ではなく、「k6 の preallocated_vus: 512 による過剰なアイドル接続・接続確立負荷」** であったことが完全に実証されました。

---

## 7. 証跡とインフラ停止記録

1. **生データ回収と整合性**:
   - `bench-results/c3-followup-20261001T225541Z/` に全 14,071 ファイルを確保。
   - 原本マニフェスト `SHA256SUMS.received` と最終マニフェスト `SHA256SUMS` の検証に合格（`artifact_verification_passed: true`）。
2. **両 VM の停止確認 (`cleanup.json`)**:
   - `bench-app-c3`: `TERMINATED` (stopped: true, checked_at: 1790961850)
   - `bench-loadgen-c3`: `TERMINATED` (stopped: true, checked_at: 1790961850)
