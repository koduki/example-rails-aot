# Rails / Roundhouse / Spinel ベンチマーク実行契約と運用手順書

更新日：2026-09-27  
対象マイルストーン：Issue [#19](https://github.com/koduki/example-rails-aot/issues/19)（GCE へ移せる実行契約・full 設定・再現手順）

> [!IMPORTANT]
> **GCE 実行に関する事前明記（未検証の境界）**:
> 本文書に記載する GCE（Google Compute Engine）構成および環境テンプレートは、**将来の専用インスタンス移行に向けた実行契約・設計仕様**です。現時点では GCE インスタンスの実作成・本測定は実施しておらず、**Linux x86-64 + Docker 実行契約に基づくスタンドアロン環境および GitHub Actions runner 上で検証済み**です。専用環境での検証済みと混同しないでください。

---

## 1. システム実行契約 (Execution Contract)

本ベンチマークスイートは、特定の CI サービスやクラウドプロバイダに依存しない **Linux x86-64 + Docker** を唯一の標準実行基盤とします。

### 1.1 前提環境要件
| 項目 | 要件 | 理由・備考 |
| --- | --- | --- |
| **OS / アーキテクチャ** | Linux x86-64 (Ubuntu 24.04 LTS 推奨) | Docker コンテナ内の各ランタイムバイナリ・glibc 互換性 |
| **Linux カーネル** | 6.x 以上 | cgroups v2 の完全サポート |
| **cgroups** | cgroups v2 統一階層 (`/sys/fs/cgroup`) | `cpu.stat`, `cpu.max`, `cpuset.cpus.effective`, `memory.current`, `memory.peak` の正確なメトリクス取得 |
| **コンテナエンジン** | Docker Engine 24.0 以上 | BuildKit サポート、cpuset/memory 制限フラグのサポート |
| **Python** | Python 3.10 以上 | 標準ライブラリのみで CLI (`scripts/bench/run.py`, `report.py`, `diagnostic.py`) が稼働 |
| **負荷生成器** | Grafana k6 v0.48+ (または Docker `grafana/k6:latest`) | Open arrival rate による offered RPS 維持と飽和検知 |

### 1.2 ホスト資源の排他的分離契約
1. **CPU の完全分離 (CPU Pinning)**:
   - アプリケーションサーバーコンテナと負荷生成器（k6）は、同一物理コアの SMT スレッドを含めて競合しないよう、排他的な CPU セットに固定（Pin）する必要があります。
   - `allocation()` 関数は論理 CPU セットの重複と許容範囲だけを検証します。SMT の物理コア重複までは検証しません。実機の `thread_siblings_list` を確認し、両セットの兄弟スレッドが交差しないように割り当ててください。
2. **メモリ予算 (Memory Budget)**:
   - コンテナごとに `--memory` および `--memory-swap` を同値に設定し、スワップアウトによるレイテンシの歪みを抑止します（デフォルト: 3072 MB〜4096 MB）。
3. **スタンドアロン CLI 動作**:
   - `GITHUB_*` や `CI` 等の環境変数が一切存在しない環境でも、CLI 引数および環境ファイル (`--env-file`) のみで全工程（build, preflight, run, report）が実行可能です。

---

## 2. 測定プロファイルと所要時間見積もり

すべての測定は同一のオーケストレーションロジック (`scripts/bench/run.py`) と結果スキーマを通ります。プロファイルにより反復回数、ウォームアップ、測定時間を切り替えます。

### 2.1 プロファイル一覧
| プロファイル | 用途 | 反復 | 対象 endpoint | ウォームアップ (min/max) | 測定時間 | 想定総時間 |
| --- | --- | --- | --- | --- | --- | --- |
| **`smoke.yml`** | Actions 手動 full 機能スモーク | 1 | `/articles` (1 系統) | 10s / 45s | 10s | 約 3〜5 分 |
| **`quick.yml`** | Actions 手動ディスパッチ比較 | 3 | `/articles`, `/articles.json` (2 系統) | 60s / 600s | 30s | 約 30〜60 分 |
| **`diagnostic.yml`** | JIT 相互作用診断 (Issue #18) | 1 | `/articles` (1 系統) | 15s / 60s | 15s | 約 10〜15 分 |
| **`full.yml`** | 専用ホスト向け本測定 (Issue #19) | 5 | 全 5 系統 (HTML 3, JSON 2) | 180s / 900s | 120s | 約 6〜10 時間 |

### 2.2 `full.yml` の所要時間見積もり式
試行総数 $N_{trials}$ は以下で計算されます：
$$N_{trials} = N_{targets} \times N_{endpoints} \times N_{repetitions}$$
`full.yml` の場合：
$$N_{trials} = 7 \text{ targets} \times 5 \text{ endpoints} \times 5 \text{ repetitions} = 175 \text{ trials}$$

1 試行あたりの最大所要時間 $T_{trial,max}$：
$$T_{trial,max} = T_{ready\_timeout} + T_{warmup,max} + T_{measurement} + T_{cleanup}$$
$$T_{trial,max} = 180\text{s} + 900\text{s} + 120\text{s} + 15\text{s} = 1,215\text{s} \approx 20.25\text{ 分}$$

最悪ケースの理論上限総時間：
$$T_{total,max} = 175 \times 1,215\text{s} = 212,625\text{s} \approx 59\text{ 時間}$$

実際の所要時間は、早期に安定判定（`stable_windows: 4`, CV $\le 0.05$, drift $\le 0.05$）を満たしてウォームアップが最短時間（$T_{warmup,min} = 180\text{s}$）で完了するため、想定期待所要時間は以下の通りとなります：
$$T_{trial,expected} = 30\text{s (起動)} + 180\text{s (ウォームアップ)} + 120\text{s (測定)} + 10\text{s (回収)} = 340\text{s} \approx 5.67\text{ 分}$$
$$T_{total,expected} = 175 \times 340\text{s} \approx 59,500\text{s} \approx 9.9\text{ 時間}$$

このため、`full.yml` の全体タイムアウトは **`total_timeout: 43200`（12 時間）** に設定されており、予算超過時は残りの試行が `not_run` として記録され、測定データが安全に保護されます。

---

## 3. 設定ファイルと環境テンプレート

CLI 引数または環境設定ファイル（`--env-file`）を用いて、実行パラメータを柔軟に設定できます。

### 3.1 ディレクトリ構成
```
bench/
├── profiles/
│   ├── smoke.yml           # CI 向け最小検証
│   ├── quick.yml           # 短時間パイロット比較
│   ├── diagnostic.yml      # JRuby ON/OFF・YJIT 診断
│   └── full.yml            # 専用機向け本測定
└── environments/
    ├── local-single-host.env   # 単一ホスト（ローカル/オンプレミス）
    ├── remote-loadgen.env      # 分散ホスト（別ホスト負荷生成）
    └── gce-c3-standard-4.env   # GCE c3-standard-4 テンプレート
```

### 3.2 環境変数一覧
| 変数名 | CLI オプション | 説明 | 例 |
| --- | --- | --- | --- |
| `BENCH_PROFILE` | `--profile` | 使用するプロファイルファイルパス | `bench/profiles/full.yml` |
| `BENCH_TARGETS` | `--targets` | 測定対象（カンマ区切り、省略時はプロファイル準拠） | `rails-cruby-off,spinel` |
| `BENCH_OUTPUT` | `--output` | 測定成果物の出力ディレクトリ | `bench-results/gce-run-01` |
| `BENCH_APP_CPUS` | `--app-cpus` | アプリケーションコンテナ用 CPU ID | `0` |
| `BENCH_LOAD_CPUS` | `--load-cpus` | 負荷生成器用 CPU ID | `1,2,3` |
| `BENCH_MEMORY_MB` | `--memory-mb` | コンテナメモリ上限値 (MB) | `4096` |
| `BENCH_TARGET_HOST` | `--target-host` | 負荷投入先ホスト名/IP（外部公開時） | `10.0.0.10` または `127.0.0.1` |
| `BENCH_SEED` | `--seed` | 試行順序シャッフルの乱数シード | `20260924` |

---

## 4. スタンドアロン実行手順（GitHub Actions 非依存）

ホスト上で直接、またはコンテナラッパーを用いて完全にスタンドアロンで実行・再集計する手順です。

### 4.1 CLI による直接実行
```bash
# 1. イメージのビルド
python3 scripts/bench/run.py build --output bench-results/build

# 2. 事前検証 (Preflight: 機能・DB・PRAGMA 一致確認)
python3 scripts/bench/run.py preflight --output bench-results/preflight

# 3. 本測定の実行 (環境設定ファイルを指定)
# preflight.json と隣接する preflight-manifest.json をセットで保持すること。
# ソース・対象イメージ・検証結果が変わった場合、preflight の再実行が必要。
python3 scripts/bench/run.py run \
  --env-file bench/environments/local-single-host.env \
  --preflight-file bench-results/preflight/preflight.json \
  --output bench-results/run-01

# 4. レポートの生成・再集計
python3 scripts/bench/report.py bench-results/run-01
```

### 4.2 コンテナラッパーによる実行
OS 依存（PowerShell / Linux shell）を吸収するラッパースクリプトも提供されています：
- **Linux / macOS**: `./scripts/run-bench-container.sh`
- **Windows (PowerShell)**: `.\scripts\run-bench-container.ps1`

### 4.3 成果物からの再集計契約
レポート生成スクリプト (`report.py`, `diagnostic.py`) は、**生成果物ディレクトリのみから完全に入力状態を再構築**します：
```bash
python3 scripts/bench/report.py bench-results/run-01
python3 scripts/bench/diagnostic.py bench-results/run-01
```
出力成果物：
- `summary.json`: 集計データと SLO 判定。固定 offered RPS の k6 測定から容量倍率は算出しない
- `summary.md`: GitHub Flavored Markdown 形式のレポートテーブル
- `summary.csv`: 機械可読な全 endpoint メトリクス一覧
- `diagnostics.md` / `diagnostics.json`: JIT 内部統計およびウォームアップ推移解析

---

## 5. 将来の GCE 移行計画と実行契約 (GCE Runbook)

将来 GCE インスタンス上で本測定を実施する際の技術仕様および運用チェックリストです。
`full.yml` は175試行で、ウォームアップ最小値と測定だけで14時間35分を要します。総予算は60時間に設定し、GitHub hosted Actions の実行選択肢から外しています。専用ホストで対象・endpointを絞る場合は `--targets` と別 profile を使い、計測条件を artifact に残してください。現在の CLI はアプリと k6 を同じホストで起動します。別ホスト負荷生成は未実装です。

### 5.1 推奨マシンタイプ
- **`c3-standard-4`** (4 vCPU, 16 GB メモリ, Intel Xeon Sapphire Rapids)
- **`c3-standard-8`** (8 vCPU, 32 GB メモリ, Intel Xeon Sapphire Rapids)
> **選定理由**: C3 シリーズは最新の NUMA 最適化と安定した L3 キャッシュスループットを提供し、共有コア型（E2/N2 の低構成）に比べてバックグラウンドの CPU スティールやクロック変動ノイズが極めて小さいため。

### 5.2 CPU トポロジと SMT の考慮
- GCE の vCPU はハードウェアハイパースレッド（SMT）として提供されます。
- 以下は 4 vCPU が物理コア 2 個 × 2 スレッドとして見える場合の割り当て例です。実機の CPU 番号と兄弟関係を測定前に確認してください。
  - Core 0: vCPU 0, vCPU 2
  - Core 1: vCPU 1, vCPU 3
- **推奨ピン留め戦略**:
  - アプリケーションコンテナ：`vCPU 0`（単一コア、スレッド干渉回避）
  - 負荷生成器（k6）：`vCPU 1, 3`（別物理コア全体を使用）
  - `vCPU 2`：アプリと k6 の CPU セットから除外。ただし OS のプロセスや割り込みをこの CPU に固定する処理は CLI に含まれません。

### 5.3 GCE 実行時に記録すべきテレメトリ項目チェックリスト
GCE 測定を実施する際は、結果の信頼性を担保するため、以下のシステム構成情報を `env.json` に記録します：
- [ ] **マシン仕様**: マシンタイプ、ゾーン、CPU モデル名（`lscpu` / `/proc/cpuinfo`）
- [ ] **トポロジ**: NUMA ノード構成（`numactl -H`）、SMT スレッド配置（`/sys/devices/system/cpu/cpu*/topology/thread_siblings_list`）
- [ ] **OS & カーネル**: ディストリビューションバージョン、`uname -a`、カーネルブートパラメータ
- [ ] **cgroups 構成**: cgroups v2 マウント状態、コントローラー有効化状態
- [ ] **ストレージ**: ディスク種類（Hyperdisk Balanced / pd-ssd）、マウントオプション、WAL 性能
- [ ] **ネットワーク**: 分散構成時の内部 VPC レイテンシ（`ping -c 100` のジッター測定）
- [ ] **バックグラウンドノイズ**: 測定前後の idle CPU 使用率（0.5% 以下であることを確認）
# Benchmark-only CRUD scope

`bench/profiles/crud.yml` uses the 90% read / 10% valid update scenario;
`bench/profiles/crud-create-delete.yml` uses a valid create/delete cycle. The
canonical `blog/` Rails 8.0.5.1 fixture disables CSRF verification in
`config/application.rb`, matching the current Roundhouse output; its derived
benchmark copy retains that policy. This is an explicit scope decision for an
AOT experiment, not evidence that either server safely rejects forged requests.

The preflight still records invalid HTML and JSON writes. Its invalid-CSRF
case is excluded because the canonical and benchmark Rails references do not
reject the token. Those cases are
**not** eligible and are not measured as equivalent operations. The CRUD gate
checks the successful operations used by the selected
scenario: `update` for `mix`/`update`, `create` plus `delete` for
`create_delete`. A missing or failed required case blocks the run. The `read`
scenario also requires the selected GET endpoint to pass preflight.
The preflight artifact and Actions summary show each target's observed
invalid-token HTTP status and whether a write persisted. An `excluded` CSRF
case is an explicit limit of this fixture, even when valid CRUD is `verified`.

Run the preflight against freshly built images before using the CRUD profile.
Keep `preflight.json` and its adjacent `preflight-manifest.json` together.
Reuse checks the source revision, runner and preflight code, target matrix,
image IDs, and result digest; changes require another preflight:

```sh
python3 scripts/bench/run.py build --profile bench/profiles/crud.yml --output bench-results/build
python3 scripts/bench/run.py preflight --profile bench/profiles/crud.yml --output bench-results/preflight
python3 scripts/bench/run.py run --profile bench/profiles/crud.yml \
  --preflight-file bench-results/preflight/preflight.json --output bench-results/crud
```

In manual Actions `full` mode the benchmark workflow runs both short CRUD scenarios after
the read smoke trial (2 operations/s for read/update, 1 operation/s for
create/delete), across all nine runtime/JIT configurations. These profiles set `verification_only: true`: they run a fixed
minimum warmup, continuing until a clean final window if startup had errors,
and mark successful trials `verified`, without claiming that
five-second windows with only a few requests establish latency convergence.
The runner rejects failed operations, dropped iterations, and client saturation.
It also checks the SQLite state after each timed trial: updates must contain
the values sent by k6, while create/delete must leave no new articles or
comments and must advance the article sequence by the number of completed
operations. A redirect alone does not certify persistence. CI fails if any
target is not `verified`.

The first retry run ([Actions run 36360930975](https://github.com/koduki/example-rails-aot/actions/runs/36360930975))
hit a single 5-second DELETE timeout in the first create/delete warmup window
on emitted JRuby with JIT off. The database contained the original 100
articles afterward, and the other eight targets verified. Functional smoke
now continues up to its configured warmup limit until the final window has no
failed operation, while retaining the earlier failure count in the trial.
Create/delete also checks that warmup left the fixture article count intact;
the timed interval still requires every operation and database effect to pass.

Ordinary PR and main pushes run the quick gate only: harness unit/CLI dry-run
checks and Terraform validation. `Rails to Spinel AOT Pipeline` runs Rails
baseline tests only. Neither quick result certifies nine-runtime image builds,
endpoint/CRUD behavior, or capacity. Dispatch both workflows with mode `full`
when those longer checks are needed; keep the commit SHA and artifacts with
the review. Formal c3 capacity is run separately on two GCE VMs, with the
procedure in `.agents/skills/gce-benchmark-runbook/SKILL.md`.

Run `Benchmark Pipeline` with `workflow_dispatch`, mode `full`, and select
`ci-jruby-convergence` to compare the four JRuby Rails / emitted and JRuby JIT
on / off combinations. The long convergence pilot is opt-in. It uses one read endpoint, three rotated
repetitions, a 60–600 second warmup, and a strict convergence gate. An
`unstable` trial fails this step; it cannot contribute to the pairwise report.
The worst configured warmup plus measurement time is 126 minutes for 12 trials,
plus startup and reporting. These closed-loop results are hosted-runner pilot
observations; passing convergence does not establish maximum capacity, a
guaranteed JRuby compiler phase, or a causal JIT speedup. Read the measured
`warmup.json`, runtime probe, and trial status before interpreting ratios.

The first strict pilot ([Actions run 36353784020](https://github.com/koduki/example-rails-aot/actions/runs/36353784020))
found 8 `unstable` and 4 `passed` trials. Several JRuby streams had steady
throughput and p95 late in their 600-second warmup but a few HTTP errors in
every window; the former gate required *zero* errors per window and therefore
could not declare convergence. The driver now retries an idempotent GET once
after a transport exception and records `transport_retries` and `error_types`.
The pilot accepts at most 0.5% failed warmup requests in each stability window;
timed requests must stay below 0.1% failures. Non-200 responses remain errors.
The report requires all three valid repetitions for each target before
publishing a pairwise ratio, and marks partial series incomplete. These
thresholds are an orchestration pilot policy, not evidence of an error-free
application or an SLO-compliant production service.

The corrected [Actions run 36362615236](https://github.com/koduki/example-rails-aot/actions/runs/36362615236) passed all 12 JRuby trials and both nine-target functional CRUD checks. On `/articles`, median RPS was 301.41 for Rails JRuby JIT on, 95.08 for off, 2257.07 for emitted JIT on, and 1033.87 for off. The emitted/Rails median RPS ratios were 7.488 (on) and 10.874 (off). Rails JIT-on repetitions were 105.0, 301.4, and 313.8 RPS, a large between-run spread despite each passing its local four-window test. The ratios remain preliminary hosted-runner observations; a local stability gate does not certify the same long-term JIT plateau across repetitions. Transport retries are recorded per trial and included in request latency.

The 2026-09-27 PR run at [Actions run 36307291456](https://github.com/koduki/example-rails-aot/actions/runs/36307291456)
finished with 7 `passed` read/update trials and 4 `passed` plus 3 `unstable`
create/delete trials, despite zero HTTP failures or dropped iterations. Its
five-second warmup windows contained roughly five operations, so their p95
fluctuation did not support a convergence claim. Those historical results are
not a seven-target comparable create/delete measurement. This PR CI contract
instead verifies functionality and persisted state; `verified` trials remain
excluded from the statistical report. For latency comparisons, copy a CRUD
profile, remove `verification_only`, use longer windows, warmup and repeated
measurement periods, and reject `unstable` trials on a dedicated host. A fixed
offered rate still does not establish maximum capacity or a JIT speedup ratio.

The subsequent [Actions run 36320457786](https://github.com/koduki/example-rails-aot/actions/runs/36320457786)
verified all nine targets in both scenarios: 9/9 `verified` for read/update and
9/9 for create/delete. Every `database_check` passed, with zero failed HTTP
requests and zero dropped iterations. The [raw artifact](https://github.com/koduki/example-rails-aot/actions/runs/36320457786/artifacts/10932791366)
contains the preflight, per-trial measurements, database snapshots, and
resource telemetry. These are functional checks under low offered load, not
steady-state latency or capacity estimates.
