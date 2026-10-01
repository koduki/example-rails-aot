# C3追加再試験指示書：主要比較の補強とSpinelの切り分け

2026-10-02作成。[Rails × Roundhouseレポート](roundhouse-rails-jit-aot-report.md)と[全反復の分析](gce-c3-retest-analysis-20261001.md)を読んでから実施する。既存の1,000記事C3結果でレポートは作成できる。追加試験は同負荷の資源比較、反復の再現性、未確定な原因を補強するために行う。

## 1. 実施順序と予算

一晩の9構成一括実行へ戻さず、以下を独立したrunとして実施する。各段階の生ログを回収・分析してから次へ進む。JRuby Offは今回の主要比較に含めない。

| 優先 | experiment | 対象・反復 | 目的 | 全experimentの実行上限 |
| --- | --- | --- | --- | --- |
| 1 | matched-rate | 主要6構成 ×3 =18件 | 共通10 RPSでp99・CPU・container memoryを比較 | 6時間 |
| 2 | capacity-cruby | CRuby Rails/emit ×Off/YJIT ×5 =20件 | 2×2比較と境界の再現性 | 8時間 |
| 3 | capacity-jruby | JRuby JIT有効のRails/emit ×5 =10件 | warmup・capacity反復の再現性 | 6時間 |
| 4 | spinel-connections | 10/15/25 RPS ×pool 10/512 ×3 =18件 | VU pool設定と接続状態の関連を切り分け | 4時間 |
| 条件付き | trace-cruby | CRuby4構成 ×3 =12件 | 同10 RPSでYJIT/GC診断 | 4時間 |
| 条件付き | trace-jruby | JRuby2構成 ×3 =6件 | 同10 RPSでJFR/JIT/GC診断 | 4時間 |

上限はbuild/preflight/転送を含まない。warmupは180–900秒であり、上限内の完走を保証しない。未完了の反復はnot_run/interruptedのまま保存する。Spinelの6セルを合わせた上限が4時間であり、各セルのprofileにある2時間を6倍して12時間実行する設計ではない。終了処理に最大約2分の猶予がある。

traceは同負荷でも差が再現し、内部処理を調べる必要が残った場合に行う。計測ありの結果は別cohortとし、正式容量の数値へ混ぜない。Spinelのpool設定変更は実際の同時接続数そのものを指定する操作ではない。connection reuseやスケジューリングへの影響は生ログから検証する。

## 2. sourceと環境の固定

実施対象はこの指示書・`retest_plan.py`の変更をマージした**同じ不変commit SHA**。GitHubのマージ済みPRから40桁SHAを取得し、`SOURCE_SHA`へ記入する。build/preflight/全experimentをそのSHAで揃える。前回の`4ae7f78`のpreflightを再利用しない。

既存のprivate `bench-app-c3`と`bench-loadgen-c3`、既存VPC/subnet/NATを使う。GCE操作は実施担当者が行う。新規VMが必要なら[runbook](../.agents/skills/gce-benchmark-runbook/SKILL.md)のTerraform手順で構成を確認する。

App VMのBashで、実際の値を設定する。以下のplaceholderを残したまま実行しない。

```bash
export PROJECT='実際のproject ID'
export ZONE='asia-northeast1-b'
export TESTER='bench-loadgen-c3'
export APP_IP='実際のApp private IP'
export SOURCE_SHA='マージcommitの40桁SHA'
export RUN_ID="c3-followup-$(date -u +%Y%m%dT%H%M%SZ)"

git fetch origin "$SOURCE_SHA"
git checkout --detach FETCH_HEAD
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
test -z "$(git status --porcelain)"
mkdir -p "bench-results/$RUN_ID"
git rev-parse HEAD > "bench-results/$RUN_ID/source-sha.txt"
```

VMは両方RUNNING、startup完了、App上のDocker権限、testerへのprivate SSH、k6を確認する。App cpusetは0,1,2,3、メモリ上限14,336 MiB。CPU topology、runtime/JIT、SQLite版、image IDを`env.json`で確認する。他の負荷・buildを測定に重ねない。

## 3. buildと正確性ゲート

App VMから別testerを指定し、全imageを一度buildして新しいpreflightを保存する。

```bash
python3 scripts/bench/run.py build \
  --profile bench/profiles/gce-c3-capacity.yml \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE" \
  --output "bench-results/$RUN_ID/build"

python3 scripts/bench/run.py preflight \
  --profile bench/profiles/gce-c3-capacity.yml \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE" \
  --output "bench-results/$RUN_ID/preflight"
```

両コマンドのexit codeは0を要求する。`preflight.json`で各実施対象の`/articles?page=1`がeligibleであることを確認する。manifestはsource・image・target coverageを照合する。image/codeが変わったらpreflightを作り直す。fixtureは1,000記事・1,000コメント、20記事の同じ順序・内容であることを確認する。`complete=false`や除外項目は保存し、primary pageの適格性をアプリ全体の同等性へ拡張しない。

## 4. 計画確認と実行

まずplanだけ生成する。これはVMや負荷を起動しない。

```bash
python3 scripts/bench/retest_plan.py \
  --experiment matched-rate --output "bench-results/$RUN_ID/preview-matched"
```

JSON profileと`retest-plan.json`のtarget/反復/budget/argvを確認する。実行時は**別の新しい出力先**と`--execute`を使う。previewの出力先は上書きできない。

```bash
python3 scripts/bench/retest_plan.py \
  --experiment matched-rate --output "bench-results/$RUN_ID/matched" \
  --execute --preflight-file "bench-results/$RUN_ID/preflight/preflight.json" \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE"
```

長時間処理はAppのtmux内で実行する。設定した環境変数は新しいtmux shellでも確認し、stdout/stderrとexit codeをroot run内へ保存する。`retest-results.json`のexit codeと各runの`trials/per-run.json`を照合する。SSH断だけで途中成果物を失わない。

次の段階では上の`--experiment`と`--output`をそれぞれ変更する。全6種類を無条件に連続実行しない。

| 段階 | experiment / 新出力先の末尾 |
| --- | --- |
| CRuby容量 | capacity-cruby / capacity-cruby |
| JRuby容量 | capacity-jruby / capacity-jruby |
| Spinel接続 | spinel-connections / spinel-connections |
| CRuby trace | trace-cruby / trace-cruby |
| JRuby trace | trace-jruby / trace-jruby |

plannerは各experiment全体のdeadlineで子runnerを停止し、猶予後に必要ならkillする。`retest-results.json`にbudget_exhausted/interrupted/not_runを残す。外部のジョブ上限を使う場合はplanner上限より少なくとも1時間長くし、build/preflight/回収時間は別に確保する。強制killや接続断後はrunner/container状態を確認し、生ログ回収と両VM停止を続行する。

## 5. 判定と観測

全experiment：fresh container/fixtureを各反復で使用。warmupは4 VU closed loop、30秒窓、180–900秒、4連続安定窓、CV/drift 0.08。エラーを含む収束や`allow_unstable`による成功扱いは認めない。

主要baselineはdiagnostics/socket/JFRを無効にする。poolは512/max4096、測定120秒。p99 ≤100 ms、error rate <0.001、drops=0、integrity合格、tester CPU <85%・network errors=0・remote telemetryあり、App sampleあり・CPU throttle増加0・OOMなしを要求する。固定レートにも`measurement_slo_required`を適用する。

matched-rateではRPSは共通10に制限される。RPS比を容量差と解釈せず、同一の測定区間におけるlatency・CPU・memoryを比較する。container memoryはbytes/2^20のMiBでRSSではない。CPU収集とk6時間にはremote orchestrationの差があるため、`phase.json`のwall_secondsとmeasurement_elapsed_secondsを確認し、区間のずれも報告する。

capacityは前回の欠けたrepへ継ぎ足さず、新cohortで各5反復を評価する。CRuby内/JRuby内の同一反復の有効pairだけを使う。CRubyとJRubyは別runなので全処理系の完全な同時比較ができたとは扱わない。capacity intervalのoffered下限・失敗上限・上限probe秒数・幅・toleranceを残す。

recoveryの応答healthは1 RPS・p99 ≤2,000 ms、性能SLOは100 msのまま別判定する。`healthy_probe`でも`performance_slo_decision=slo_fail`になり得る。`server_queue_drained=null`を維持し、低負荷成功からserverの未完了処理ゼロを断定しない。

| 成果物 | 観測内容と限界 |
| --- | --- |
| 各phaseのphase.json | 設定duration、開始/終了、wall time、k6 elapsed、失敗。wall timeだけでSSHが支配的とは断定しない |
| k6-invocation.json | 実際に渡したMODE/RATE/VU pool/TIMEOUT。closed warmupとopen measurementを識別 |
| telemetry.json、cpu-before/after、tester-* | App/client有効性。欠損を0に置換しない |
| recovery-*/recovery.json | healthとperformance判定を分離。queue drainageは未観測 |
| 各phaseのsocket-observations.json | Spinel診断のみ。2秒ごとのIPv4/IPv6 TCP stateとraw procデータ、non-LISTEN byte queue。LISTENのraw queueはbyteやHTTP queueと呼ばない |
| server.log、diagnostics.json | traceのYJIT/GC/JIT統計・ログ。runtimeにより利用できる項目が異なる |
| jfr-capture.json、data/bench.jfr | trace-jrubyのみ。profile設定のrolling recording、最大128 MiB/10分。終了時dump、失敗理由・実ファイルsizeを保存。capturedはevent解析完了を意味しない |

JRuby traceではJava 21 JDKの`jcmd`が使えることを確認する。回収コピー上で`jfr summary data/bench.jfr`が読めること、warmup/measurement時刻に対応するevent、execution/allocation/GC/compilation/monitor等の実際の収録状況を確認する。event未収録を「待ちゼロ」と解釈しない。[JDK 21 jcmd公式仕様](https://docs.oracle.com/en/java/javase/21/docs/specs/man/jcmd.html)を参照。長い試行では最後の10分しか保持されないため、過去の全warmupを観測したとは主張しない。

SQL/materialization/preload/renderの個別time、HTTP active-request/worker gauge、Spinel内部stackは今回追加していない。JFR/socketでも原因が分離できなければ次の処理段階計測を提案する。HTTP timeoutの記録からDB待ちやserver queue残存を断定しない。

## 6. 回収・停止・提出

終了・失敗どちらでもroot run全体を保存する。build/preflight、全profile、retest-plan/results、各trialの全phase・失敗ログ、env/plan、runtime/launch/container、JFRを含める。停止前にApp側の処理が終了していることを確認し、root runでSHA256SUMSを生成する。

App VMのrepoで：

```bash
python3 - "$RUN_ID" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, 'scripts/bench')
from run_gce_suite import verify_artifact_checksums
verify_artifact_checksums(Path('bench-results') / sys.argv[1])
PY
```

実施担当者の端末へroot runをIAP経由で丸ごと取得する。以下の`APP_REPO_PATH`とlocal保存先は実際の値に置き換える。

```bash
gcloud compute scp --recurse --tunnel-through-iap \
  --project "$PROJECT" --zone "$ZONE" \
  "bench-app-c3:APP_REPO_PATH/bench-results/$RUN_ID" "LOCAL_PARENT/"
```

ローカルのSHA256SUMSを先に検証し、原本manifestのコピーを保持する。その後、実施端末のrepoから両VMを停止する。

```bash
python3 scripts/bench/gce_cleanup.py --project "$PROJECT" --zone "$ZONE" \
  --status-file "LOCAL_PARENT/$RUN_ID/cleanup.json" \
  --verify-checksums "LOCAL_PARENT/$RUN_ID"
```

cleanupは受信manifestを検証してからcleanup.jsonを書き、既知のローカル停止証跡を含む最終manifestへ更新する。最初の受信manifestをSHA256SUMS.receivedとして保持し、再停止時も現在のmanifestを先に検証する。hash不一致では失敗を返し、古いmanifestを成功扱いで置き換えず、両VMの停止は続行する。両方のtimestamp付きTERMINATEDを確認する。停止失敗なら担当者が再停止・再確認し、証跡を残す。ディスク削除は生ログ確保後に当該Terraform stateの所有資源だけを対象とする。

機械reportを再生成する場合は受信hashを検証した後の**作業コピー**で各cellに`run.py report --output CELL_PATH`を使う。`run_gce_suite.py`は従来の単一profile用なので、今回の複数experimentを一コマンドで実施したものとは扱わない。

提出物はrelease URL、archive SHA-256、source SHA、experiment別全反復・全phase、生ログ、受信/最終manifest、両VM停止記録。分析では主要baselineとtrace/Spinel診断を区別し、失敗・未実行を含めた表、同負荷の資源比較、paired ratioの有効数、残った仮説を報告する。
