# ベンチマーク再設計 要件定義書 / 修正依頼書

更新日: 2026-09-28  
対象リポジトリ: `koduki/example-rails-aot`  
基準コミット: `c36d2ce9a133a89cbeb53183130b101a409b97a9`

## 1. 目的

本書は、Rails / Roundhouse 生成 Ruby / Spinel を CRuby・JRuby・AOT の各実行系で比較する現行ベンチマークについて、以下の2系統を明確に分離して再設計するための要件を定義する。

1. **GitHub Actions 簡易検証**
   - PR / push 時に短時間で実行する品質ゲート。
   - 機能一致、ベンチコードの健全性、JIT/ランタイム設定の確認、短時間 smoke の成立確認を目的とする。
   - **最大性能や処理系順位を主張しない。**

2. **GCE c3-standard-4 × 2台 本測定**
   - `c3-standard-4` を **アプリサーバ1台 + 負荷生成テスター1台** の2台構成で用意する。
   - 9構成を同一ソース・同一fixture・同一アプリVM・同一負荷条件で直接比較する。
   - 最大持続スループット、p99、CPU、メモリ、ウォームアップ、JIT相互作用を正式な性能評価として扱う。

主目的は、現在の「異なる実験世代・異なる測定条件を混在させたためCRuby/JRuby/Spinelを直接比較できない」という制約を解消し、**同一ハードウェア予算上でのシステム性能を直接比較可能にすること**である。

---

## 2. 現行コードで確認した課題

### 2.1 Terraform が1台構成

現行 `infra/terraform/main.tf` は `google_compute_instance.bench_runner` 1台のみを作成する。

- VM名: `bench-runner-c3`
- machine type: `c3-standard-4`
- Docker / k6 を同一VMへインストール
- private IPのみ
- default VPC / default subnet を使用

今回の本測定では、**SUT（アプリ）と負荷生成器を物理VMで分離する必要がある。**

### 2.2 GCE環境ファイルが旧1CPU分離モデル

`bench/environments/gce-c3-standard-4.env` は以下の想定になっている。

- app: vCPU 0
- load generator: vCPU 1,3
- vCPU 2: reserve

これは1台のVM内でCPUを分離する旧モデルであり、今回の要件である

- app VM: 4 vCPUをアプリに使用
- tester VM: 4 vCPUをk6に使用

とは一致しない。

### 2.3 `quick-4core.yml` は単一 c3-standard-4 では成立しない

現行 `quick-4core.yml` は

- `cpu_count: 4`
- 「4 app CPUs + distinct load generator CPUs」

を要求している。

`c3-standard-4` 1台では app に4 vCPUを使った時点で load generator 用CPUが残らないため、**本来は2台構成が必要**である。

### 2.4 `full.yml` が容量測定になっていない

現行 `full.yml` は

- 7構成のみ
- `cpu_count: 1`
- `offered_rps: 50`

である。

50 RPS固定では、数百〜数千RPS処理できる対象の**最大持続性能を測れない**。

また open-arrival の固定低負荷でRPSのCV/driftを見ても、offered rate自体が固定されているためJIT収束の判定として弱い。

### 2.5 JRuby OFF が主比較から外れている

現在の主profileは7構成だが、正式比較では以下の9構成を同一条件で測る必要がある。

| Shape | Runtime | JIT / compile mode |
| --- | --- | --- |
| Rails | CRuby | YJIT Off |
| Rails | CRuby | YJIT On |
| Rails | JRuby | compile.mode=OFF |
| Rails | JRuby | compile.mode=JIT |
| Roundhouse生成 | CRuby | YJIT Off |
| Roundhouse生成 | CRuby | YJIT On |
| Roundhouse生成 | JRuby | compile.mode=OFF |
| Roundhouse生成 | JRuby | compile.mode=JIT |
| Roundhouse生成 | Spinel | AOT |

### 2.6 remote load generator は未実装

`run.py` の `target_host` は接続先URLを変えるが、k6自体は

- ローカル `k6`
- またはローカル Docker の `grafana/k6`

として実行される。

従って、現行コードは**別VMから負荷を発生させる実装になっていない**。

### 2.7 1000件fixtureと「ページング」が主測定に接続されていない

`scripts/bench/prepare.py` は大量fixture生成と `fetch_page()` を持つが、

- `full.yml`
- `quick-4core.yml`

は `fixture_articles` を指定していないためデフォルト3件のままである。

さらに、実アプリの `ArticlesController#index` は

```ruby
@articles = Article.includes(:comments).order(created_at: :desc)
```

であり、**20件ページングは実アプリには実装されていない**。

従って、「1000件fixture + 20件ページング」の性能シナリオを名乗る場合は、実際のRailsコードとRoundhouse生成物に同じページング経路を実装し、preflightで一致を確認する必要がある。

### 2.8 統計処理がpaired designを十分利用していない

現行 `report.py` は基本的に各targetの中央値を先に求め、その中央値同士を割って倍率を出す。

正式比較では、試行順をrotationしている利点を活かし、

```text
rep1: emitted / rails
rep2: emitted / rails
rep3: emitted / rails
...
```

のように**同じ反復番号のペアごとに比率を計算し、その分布を集計**すべきである。

### 2.9 SLO判定に「中央値p99」を使わない

現在のレポートは各反復p99の中央値をSLO判定に使う箇所がある。

正式測定では、1回だけ大きく劣化した試行を中央値で隠してはいけない。

- 各反復を個別にSLO判定する
- worst p99
- worst error rate
- dropped iterations
- client saturation

を明示する。

### 2.10 SQLite write contention の説明修正

CRUDで異なるarticle idへ更新先を分散するのは有効だが、これは

- 同一行への論理競合

を避けるものであり、

- SQLite writer lock contention

自体を消すものではない。

文書とコメントは「SQLiteロック競合を防止」ではなく、**同一レコード競合を回避し、SQLiteの単一writer制約は別途観測する**という説明に修正する。

### 2.11 Spinelの解釈

SpinelとCRuby/JRubyでは

- native化
- HTTPランタイム
- SQLite adapter
- scheduling
- memory management

も変わる。

従って「AOTコンパイラ単体で○倍」とは主張しない。

一方で、これらはSpinelの設計に含まれるため、

> **Roundhouse + Spinel 実行アーキテクチャ全体の成果**

としてシステム比較することは妥当である。

---

# 3. 全体アーキテクチャ要件

## 3.1 GitHub Actions 系

用途:

- correctness
- regression detection
- benchmark harness validation
- runtime/JIT configuration validation
- short smoke

性能順位の公開は禁止する。

```text
GitHub Hosted Runner
 ├─ build 9 targets
 ├─ preflight
 ├─ short smoke
 ├─ CRUD functional verification
 ├─ unit tests
 └─ CI verification report
```

## 3.2 GCE 正式測定系

```text
                same zone / private VPC
┌──────────────────────────┐
│ bench-loadgen-c3         │
│ c3-standard-4            │
│                          │
│ k6                       │
│ load-generator telemetry │
└────────────┬─────────────┘
             │ internal VPC
             │ HTTP
             ▼
┌──────────────────────────┐
│ bench-app-c3             │
│ c3-standard-4            │
│                          │
│ Docker SUT               │
│ app uses 4 vCPU budget   │
│ runtime telemetry        │
└──────────────────────────┘
```

要件:

- 2台とも `c3-standard-4`
- 同一zone
- private IP通信
- 外部IPなし
- app VMとtester VMの役割を分離
- app VMではk6を動かさない
- tester VMではSUTを動かさない
- 測定用通信はtester → appのみに限定

---

# 4. Terraform 改修要件

## 4.1 VMを2台へ分離

最低限、以下を作成する。

- `bench-app-c3`
- `bench-loadgen-c3`

両方:

- machine type: `c3-standard-4`
- Ubuntu 24.04
- 同一zone
- private NIC
- OS Login
- role-specific tag

推奨tag:

- `bench-app`
- `bench-loadgen`

## 4.2 startup script を役割別に分離

現行 `startup.sh` の全部入り構成を以下へ分ける。

### app VM

必須:

- Docker Engine
- git
- Python
- build tools
- numactl
- CPU topology logging
- cgroups v2 validation

k6は不要。

### loadgen VM

必須:

- k6
- git
- Python
- jq
- CPU/network telemetry

Dockerは必須ではない。使用するなら理由を明示する。

## 4.3 firewall

最低限:

1. IAP SSH
   - source: Google IAP range
   - target: app / tester

2. benchmark HTTP
   - source: `bench-loadgen` tag
   - target: `bench-app` tag
   - port: 固定 benchmark port（推奨 3000）

アプリポートをVPC全体へ公開しない。

## 4.4 app port を固定可能にする

現行 `run.py` はDocker host側portをランダム割当している。

remote testerから接続する正式GCE modeでは、同時に1 SUTのみ起動するため、

- app host port = 3000

など固定portを使用できるようにする。

local/Actions modeは従来のrandom portでもよい。

## 4.5 network / NAT

現行Terraformは「既存Cloud NATを使う」とコメントしているが、Terraform自身はNATを作成していない。

**暗黙の既存リソース依存は禁止する。**

次のどちらかを必須とする。

- Terraformがdedicated VPC / subnet / Cloud Router / Cloud NATを作成
- または既存network/NATをvariableで明示指定し、preflightで存在確認

再現性の観点では前者を推奨する。

## 4.6 outputs

最低限以下を出す。

- app instance name
- app internal IP
- loadgen instance name
- loadgen internal IP
- zone
- app SSH command
- loadgen SSH command

## 4.7 project ID

`project_id` の固定defaultは削除またはexampleへ移す。

実測環境は `tfvars` / CLI variableで明示する。

---

# 5. ベンチランナー改修要件

## 5.1 LoadGenerator abstraction

`run.py` の負荷生成処理を抽象化する。

例:

```text
LoadGenerator
 ├─ LocalLoadGenerator
 └─ RemoteLoadGenerator
```

### LocalLoadGenerator

用途:

- GitHub Actions
- local smoke
- developer test

### RemoteLoadGenerator

用途:

- GCE正式測定

要件:

- tester VM上でk6を起動
- app private IPへ接続
- normalized summaryをcontrollerへ返却
- k6 raw logをartifactとして保存
- tester CPU / memory / network utilizationを記録

remote実装方式はSSH、明示的agent等のどちらでもよいが、**app VMでk6を起動しないこと**を受け入れ条件とする。

## 5.2 controller location

実装は以下のいずれかでよい。

### 推奨

`run.py` は app VM上で実行し、

- app container lifecycle
- fixture
- telemetry

を既存コードで管理しつつ、RemoteLoadGenerator経由でtester VMへk6実行を依頼する。

### 代替

外部orchestratorが2VMを制御してもよい。

ただし、成果物は最終的に1つのrun directoryに統合され、

- app metrics
- loadgen metrics
- k6 raw
- trial metadata

を同じtrial IDで辿れること。

## 5.3 GCE environment profile

既存 `gce-c3-standard-4.env` は旧1VM前提なので置き換える。

例:

```text
BENCH_PROFILE=bench/profiles/gce-c3-capacity.yml
BENCH_APP_CPUS=0,1,2,3
BENCH_MEMORY_MB=...
BENCH_REMOTE_LOADGEN=...
BENCH_TARGET_HOST=<app-private-ip>
BENCH_TARGET_PORT=3000
```

load generator側CPUはtester VM上の `0,1,2,3` を使用してよい。

---

# 6. GitHub Actions 簡易検証シナリオ

## 6.1 目的

Actionsは正式な性能測定ではない。

次を検証する。

1. 9構成すべてbuild可能
2. runtime/JIT probeが期待値と一致
3. read endpointのpreflight一致
4. CRUD正常系のHTTP/DB effect一致
5. smoke workloadが実行可能
6. harness/reportのschemaが壊れていない
7. Terraform validate / fmt
8. GCE profile dry-run
9. remote load generator adapterのunit test

## 6.2 対象9構成

Actionsのbuild/preflightでは正式測定と同じ9構成を必ず対象にする。

## 6.3 performance smoke

短時間smokeは残す。

ただしレポートでは:

- RPSを観測値として残してもよい
- runtime間のwinner/順位を出さない
- speedupを正式結果として扱わない
- hosted runnerの値をGCE結果と混ぜない

明記:

> CI smoke is a regression signal, not a capacity benchmark.

## 6.4 JRuby convergence

長時間JRuby convergenceはPR自動実行から外したままでよい。

manual workflowとして維持する。

目的:

- JIT段階変化の診断
- harnessの検証

正式なCRuby/JRuby直接比較には使用しない。

## 6.5 Actionsレポート

新規:

`ci-verification-report.md`

必須内容:

- commit SHA
- 9 targets build status
- runtime/JIT probe
- endpoint eligibility matrix
- CRUD functional verification
- trial status
- harness unit tests
- Terraform validation
- known exclusions
- smoke observations

禁止:

- 「最速」
- capacity ranking
- GCE正式結果との数値混在

---

# 7. GCE c3-standard-4 正式測定シナリオ

## 7.1 Primary question

> c3-standard-4 1台分のアプリ計算資源を与えたとき、Rails / Roundhouse生成Ruby / Spinel の各実行構成は、どの程度の最大持続処理能力・レイテンシ・メモリ効率を示すか。

## 7.2 ハードウェア条件

app:

- c3-standard-4
- 4 vCPU全部をSUTへ利用可能
- 16 GB memory budget内
- app以外の測定負荷を極小化

loadgen:

- c3-standard-4
- 4 vCPUをk6へ利用可能

記録:

- CPU model
- `lscpu`
- SMT siblings
- kernel
- Docker version
- k6 version
- disk
- cgroups
- git SHA
- image digest
- zone
- internal network RTT

## 7.3 runtime並列設定

同じ4 vCPU budget内で、各runtimeに自然な並列モデルを使用する。

初期案:

- CRuby: Puma workers = 4
- JRuby: worker = 0/1 process、threads = 4
- Spinel: workers = 4

ただし、設定は**本測定前に固定し、結果を見てtargetごとに後付け最適化しない。**

必要なら「共通設定比較」と「runtime推奨設定比較」を別実験にする。

## 7.4 Primary workload

主比較はまず1 endpointに絞る。

推奨:

`GET /articles?page=1`

条件:

- fixture: 1000 articles
- 1 articleあたり固定comments
- 実アプリ側で20件pagination
- Rails / emitted / Spinelで同一結果
- preflight通過

### ページング実装

Python helperだけでなく、実際の `ArticlesController#index` と生成物が同じ20件を返すこと。

Roundhouseが対応できない場合は、その制約をblockerとして記録し、代替workloadを新しい名前で定義する。

## 7.5 Warmup

固定低RPS open-arrivalのRPS安定だけでJIT収束を判定しない。

推奨:

- closed-loop
- 十分なconcurrency
- min 180 sec
- max 900 sec
- window 30 sec

収束判定候補:

- throughput CV
- throughput drift
- p95/p99 drift
- CPU drift
- JIT/compiler diagnostics
- error/dropなし

JRubyは、局所窓の安定だけでなく**反復間で異なる性能tierに残る可能性**をレポートする。

## 7.6 Capacity search

固定50 RPSを廃止する。

### Step 1: coarse ramp

例:

```text
100
200
400
800
1200
1600
2400
3200
4800
6400
...
```

### Step 2: bracket

SLOを初めて破る点を上限、その直前を下限とする。

### Step 3: binary / bounded search

上下の間を探索し、最大持続RPS候補を決定する。

### Step 4: sustained confirmation

候補RPSで120秒以上測定する。

## 7.7 Sustainable capacity 定義

各試行が以下をすべて満たすこと。

- error rate < 0.1%
- dropped iterations = 0
- client saturation = false
- p99 <= 100 ms（暫定。profile化する）
- OOMなし
- app CPU quota/throttle異常なし
- tester CPUに十分な余裕
- tester自身のVU上限到達なし

SLO値はprofileに明示し、後から対象別に変えない。

## 7.8 repetitions

最低5反復。

可能なら7反復。

scheduleはtarget順をrotation / reversalし、時間ドリフトを分散する。

## 7.9 paired statistics

倍率は同じrepetition同士で計算する。

例:

```text
Roundhouse effect rep1 = emit rep1 / rails rep1
Roundhouse effect rep2 = emit rep2 / rails rep2
...
```

出力:

- median
- min/max
- IQR
- paired ratios

反復数が少ない場合、見かけ上精密な95% CIを無理に出さない。

CIを出す場合は手法と標本数を明記する。

## 7.10 SLO aggregation

以下は禁止:

> median(p99 per repetition) <= SLO だからpass

代わりに:

- 各repetitionのpass/fail
- worst p99
- worst error rate
- capacity distribution

を表示する。

---

# 8. Secondary workloads

## 8.1 CRUD

1000件fixtureで実施する。

シナリオ:

- 90% read / 10% update
- update only
- create/delete

操作単位で計測する。

- operations/s
- operation p50/p95/p99
- HTTP request count
- DB state verification

SQLiteでは

- same-row logical contention
- single-writer contention

を区別して解釈する。

## 8.2 mechanism isolation

可能なら追加する。

目的:

- DB adapter差
- HTTP runtime差
- JIT/compute差

をある程度切り分ける。

候補:

- DBなしrender/compute endpoint
- DB read
- normal application endpoint

これはSpinelのシステム全体性能と、JIT機構分析を混同しないための補助実験とする。

## 8.3 1/2/4 vCPU scaling

任意P2。

同一 c3 VMでapp側のcpusetを

- 1 vCPU
- 2 vCPU
- 4 vCPU

に変えてscalingを見る。

主結果は4 vCPUとする。

---

# 9. GCE正式レポート要件

新規正式レポート:

`docs/gce-c3-benchmark-report.md`

自動生成用raw report例:

`bench-results/.../gce-summary.md`

## 9.1 必須章

1. Executive Summary
2. 実験目的
3. ハードウェア / ソフトウェア条件
4. 2VM構成図
5. correctness gate
6. warmup behavior
7. capacity search methodology
8. primary capacity result
9. latency
10. CPU / memory
11. paired Roundhouse / JIT ratios
12. JRuby warmup analysis
13. Spinel architecture result
14. CRUD
15. limitations
16. raw artifact provenance

## 9.2 Primary result table

最低限:

| Target | Sustainable RPS | p50 | p95 | p99 | Error | App CPU | Peak memory | Warmup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

## 9.3 反復の可視化

中央値だけでなく、全repetitionを表示する。

最低限:

- capacity by repetition
- p99 by repetition
- warmup duration
- memory
- paired ratio

## 9.4 Spinel表現

推奨表現:

> SpinelはAOTコンパイルだけでなく、HTTP、DBアクセス、メモリ管理、スケジューリングを含む実行スタック全体をネイティブ向けに再構成する。そのため観測差をAOTコンパイラ単体の寄与へ分離することはできない。一方、これらはSpinelの設計に含まれるため、Roundhouse + Spinel実行アーキテクチャ全体の成果として評価する。

避ける表現:

> AOTにしただけで○倍高速化

## 9.5 CI結果との分離

Actions smokeのRPSとGCE正式容量を同じ性能表に混在させない。

既存のhistorical Actions結果は

- historical pilot
- CI observation

として残してよい。

---

# 10. Monitor skill 改修要件

現行 `bench-runner-monitor` は

- single instance
- `scripts/bench/driver.py`
- single result directory

を前提としている。

GCE 2VM化後は以下を表示する。

## app VM

- runner process
- current target
- current phase
- active Docker container
- app CPU
- app memory
- throttle
- current port

## tester VM

- k6 process
- current offered RPS
- VUs
- tester CPU
- dropped iterations
- network errors

## progress

capacity searchは1 targetにつき複数rate stepを持つため、

- target
- repetition
- phase
- rate step
- confirmation

を表示する。

単純な `trial count / schedule count` だけではETAを出さない。

---

# 11. テスト要件

## 11.1 unit tests

追加:

- 9-target profile contract
- remote loadgen adapter
- fixed app port in remote mode
- remote summary parsing
- remote failure handling
- loadgen saturation rejection
- capacity bracket search
- binary search
- sustainable capacity decision
- paired ratio calculation
- per-repetition SLO
- 1000 fixture
- actual pagination preflight
- 2VM monitor parser

## 11.2 Terraform tests

最低限CIで:

```bash
terraform fmt -check
terraform init -backend=false
terraform validate
```

可能なら:

- resource count = 2
- machine type = c3-standard-4
- no external IP
- firewall source/target roles
- outputs existence

を静的テストする。

## 11.3 integration dry-run

Actions上では実VMを作らず、

- GCE profile parse
- 9-target schedule
- capacity search plan
- remote loadgen command build
- report generation

までdry-runする。

---

# 12. 成果物

## コード

- 2VM Terraform
- role-specific startup scripts
- remote load generator support
- GCE capacity profile
- 9 target main profile
- capacity search
- paired statistics
- monitor 2VM support
- actual pagination
- tests

## レポート

### CI

`ci-verification-report.md`

### GCE

`gce-c3-benchmark-report.md`

### Raw artifacts

- plan
- environment
- app telemetry
- loadgen telemetry
- k6 raw
- warmup windows
- capacity-search steps
- per repetition results
- preflight
- image digests
- source SHA

---

# 13. 受け入れ条件

## Actions

- [ ] 9構成build
- [ ] 9構成preflight
- [ ] CRUD functional gate
- [ ] smoke完走
- [ ] Terraform validate
- [ ] GCE profile dry-run
- [ ] CI report生成
- [ ] CI reportがcapacity rankingを主張しない

## Terraform

- [ ] app / tester の2VM
- [ ] 両方 c3-standard-4
- [ ] private IPのみ
- [ ] tester → app のみbenchmark port許可
- [ ] app/tester startup role分離
- [ ] app/tester private IP outputs
- [ ] NAT/network依存が明示的

## GCE runtime

- [ ] app 4 vCPUをSUTへ使用
- [ ] tester上でのみk6を実行
- [ ] app VMでk6が動いていない
- [ ] 9構成を同じsource/fixture/profileで測定
- [ ] JRuby OFFを含む
- [ ] 1000件fixture
- [ ] 実アプリpagination
- [ ] capacity search
- [ ] 5回以上反復
- [ ] paired ratio
- [ ] per-repetition SLO
- [ ] app/tester telemetry

## Report

- [ ] CI reportとGCE reportを分離
- [ ] GCE reportで全9構成を直接比較
- [ ] historical Actions値を正式GCE値と混ぜない
- [ ] Spinelを「AOT単体」ではなく「Spinel実行アーキテクチャ全体」として解釈
- [ ] JRuby warmupの反復差を可視化
- [ ] raw artifactから再集計可能

---

# 14. 実装優先順位

## P0: GCE本測定の成立に必須

1. Terraform 2VM化
2. remote load generator
3. 9-target GCE profile
4. fixed remote app port
5. capacity search
6. per-repetition SLO
7. paired statistics
8. GCE report
9. actual 1000 fixture + pagination

## P1: 結果の解釈品質

1. tester telemetry
2. monitor 2VM化
3. CRUD正式測定
4. memory / CPU efficiency metrics
5. JRuby diagnostic integration

## P2: 機構分析

1. DBなしworkload
2. 1/2/4 CPU scaling
3. GC / allocation / compiler profile
4. SpinelのHTTP/DB層を揃えた追加対照

---

# 15. 完了の定義

この改修は、単にGCE上でベンチが実行できることをもって完了としない。

完了条件は以下である。

> **同一コミット、同一fixture、同一c3-standard-4アプリVM、同一負荷生成方式のもとで、9構成の最大持続性能を反復測定し、Actionsの簡易結果と混同せず、raw artifactから再計算可能な正式レポートを生成できること。**

この条件を満たして初めて、

- CRuby vs JRuby
- YJIT vs JRuby JIT
- Rails vs Roundhouse
- Roundhouse + Spinel

を同一実験内で直接比較できる。
