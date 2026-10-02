# C3追加試験指示書：性能逆転・ページング・SpinelのFD

作成日：2026-10-03 JST。対象：記事・コメントのRailsアプリ、Roundhouse emit（CRuby/JRuby）、Spinel。

## 1. 目的と前提

簡易検証は3記事・3コメント、短時間closed loopだった。最新試験は1,000記事・1,000コメントを読み込み、アプリで20記事を選ぶ条件で、CRuby Offではemitが遅く、YJIT有効ではRailsと性能が近い。処理量と測定方法の変更を、同じ環境の対照実験で切り分ける。

生成器には記事ごとにコメント全体を走査する関連付けの経路があるが、ハッシュによる別経路もある。実測生成物がどちらを使うかは未確認。理論上の比較回数を実測時間や確定原因として扱わない。

Spinelは最新試験の25 RPS・pool 512で3回とも`dup(2) failed for fd 1023`を記録し、pool 10は3/3成功した。実際のFD上限とerrnoをまだ取得していない。25 RPSを最大容量、負荷生成器だけを原因、とは解釈しない。

根拠は[簡易検証レポート](roundhouse-actions-20260928-report.md)、[最新releaseと全生ログ](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-followup-20261002-c3-followup-20261001T225541Z)、[使用版の関連付け生成器](https://github.com/rubys/roundhouse/blob/2e286e6f93a970fa7e93a5da2e53ee2d127f7169/src/lower/arel/visitor.rs)。最新試験のsourceは`98a1beec9a406ead2fe3b3e219086c171e1883a4`。

既存の同10 RPSの省メモリ・レイテンシ結果はレポートに利用できる。この追加試験は原因と適用範囲を補強する。householdの性能には別途、household自身のsource/fixture/workload/preflightと生ログが必要。

## 2. 実施順・対象・予算

生成物を確認してから、各セルを個別に実施する。すべてのexperimentを無条件に連続実行しない。

| 段階 | experiment | 計画trial数 | 次へ進む条件 |
| --- | --- | --- | --- |
| 0 | 実測生成コードの保存・確認 | build成果物1組 | SQL、関連付け、版・hashを確認 |
| 1A | scaling-cruby | 3/20/1,000件 × CRuby4構成 ×3 =36 | 同環境で件数による変化を確認 |
| 1B | spinel-fds | pool 10/512 × default/raised nofile ×3 =12 | default値を実測し、意味のある上限対照を設定 |
| 2 | pagination-cruby | app-sliced/db-paged × CRuby4構成 ×3 =24 | 1,000件から返す20件の内容・順序が同等 |
| 条件付き | scaling-jruby / scaling-spinel | 18 / 9 | 1Aの現象を他runtimeへ広げる必要がある |
| 条件付き | pagination-jruby / pagination-spinel | 12 / 6 | DBページングを他runtimeでも評価する |
| 条件付き | 関連付け改修の対照 | 元版/ハッシュ版 | 実生成物で二重ループを確認済み |

CRuby/JRuby/Spinelの件数・ページング各セルは最大4時間、FD各セルは最大1時間。1A全体6時間、1B全体4時間、段階2全体4時間を作業上限とし、残時間が終了・回収に足りなければ次セルを開始しない。build/回収/停止は別枠。これは完走見積もりではなく、未収束・予算不足・未実施を保存するための上限である。

FD障害が残る間はSpinelの正式容量探索を行わない。少接続での件数・ページング診断は別cohortとして実施可能。JRuby Offは追加しない。条件付きセルの実施理由を記録する。

## 3. 固定条件

- 既存のApp/loadgen C3二台、private通信、App cpuset 0–3、memory上限14,336 MiB。物理core/SMT構成を記録する。
- この変更を含むマージ済み40桁source SHAを固定。各反復はfresh container/fixture。
- 件数・ページングは同10 RPS、pool 10/max VUs 4096、120秒、各3反復。FD試験だけ25 RPS、pool 10/512。max VUsは4096で固定。
- warmupは4 VU closed loop、180–900秒、30秒窓、4連続安定窓、CV/drift ≤0.08、error/dropのない収束。未収束を許容して成功にしない。
- SLOはp99 ≤100 ms、error rate ≤0.1%、drop 0、integrity合格、client/App観測の有効性、throttle増加0、OOMなし。失敗trialも提出する。
- fixtureは1記事1コメント。3件セルの応答は3記事、20/1,000件セルは20記事。簡易再現の3件と、表示20件固定の20→1,000件の比較を区別する。
- runtime/JIT/SQLite版、PRAGMA、workers、CPU/メモリ、fixture/応答件数、source/profile/image hashを固定・記録する。SQL取得件数を応答件数から推定しない。

新baselineはpool 10であり、旧pool 512 cohortへ反復を継ぎ足さない。固定RPSから最大容量やJIT容量倍率を算出しない。p50/p95/p99、CPU、container memory peakを同条件で比較し、対応反復の比と全反復の有効数を示す。MiBはbytes/2^20、container memoryはRSSではない。CPU collectorとk6の時刻・区間も記録し、CPU%をリクエスト当たりCPU時間と読み替えない。

## 4. 準備・build・生成物

実施担当者は[GCE runbook](../.agents/skills/gce-benchmark-runbook/SKILL.md)で両VMの起動・SSH・Docker権限を確認する。指示書の作成はVM起動や負荷試験の実行を意味しない。App VMのBashで以下を実値へ変更する。

```bash
export PROJECT='実際のproject ID'
export ZONE='asia-northeast1-b'
export TESTER='bench-loadgen-c3'
export APP_IP='実際のApp private IP'
export SOURCE_SHA='この変更を含むマージ済み40桁SHA'
export RUN_ID="c3-mechanism-$(date -u +%Y%m%dT%H%M%SZ)"
export RUN_ROOT="$(pwd)/bench-results/$RUN_ID"

git fetch origin "$SOURCE_SHA"
git checkout --detach FETCH_HEAD
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
test -z "$(git status --porcelain)"
mkdir -p "$RUN_ROOT"
git rev-parse HEAD > "$RUN_ROOT/source-sha.txt"

python3 scripts/bench/run.py build --profile bench/profiles/gce-c3-capacity.yml \
  --targets rails-cruby-off,rails-cruby-yjit,emit-cruby-off,emit-cruby-yjit,rails-jruby,emit-jruby,spinel \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE" --output "$RUN_ROOT/build"
```

中間stageを同じsource・Docker build cacheから保存する。

```bash
docker build -f bench/Dockerfile --target emit -t rails-aot-bench:emit-evidence . \
  > "$RUN_ROOT/generated-build.log" 2>&1
task_container=$(docker create rails-aot-bench:emit-evidence)
mkdir -p "$RUN_ROOT/generated"
for task_target in ruby jruby spinel; do
  docker cp "$task_container:/src/out/bench-$task_target" "$RUN_ROOT/generated/"
done
docker rm "$task_container"
```

中間stageのimage IDを保存する。CRuby/JRubyの実serving imageからも`docker create`/`docker cp`で`/app/app`、`/app/runtime`、`/app/benchmark-emission.json`を取り出し、対応する中間stageのファイルとhashを照合する。Spinelはbinary hash、build log、生成Ruby、利用可能なら生成C/compile flagsも保存する。中間stageを保存しただけで実測imageとの一致を認定しない。一時containerは失敗時も除去する。

確認対象は`ArticlesController#index`、`Article/Comment.from_stmt`、comments preload、`runtime/db.rb`、ビュー。二重ループ/ハッシュ、行生成の範囲、SQLのLIMIT/OFFSETを記録する。起動成功だけでこの確認を済ませない。

## 5. 計画生成・セル単位の実行

まず計画だけ生成する。以下はDocker/GCEを起動しない。

```bash
python3 scripts/bench/mechanism_plan.py --experiment scaling-cruby \
  --output "$RUN_ROOT/preview-scaling-cruby"
python3 scripts/bench/mechanism_plan.py --experiment spinel-fds \
  --output "$RUN_ROOT/preview-spinel-fds"
```

`mechanism-plan.json`、セルのJSON profileとargvを確認する。実行はApp VMのtmux内で、previewとは別の新出力先を指定する。`--execute-cell`なしでは実行されない。

```bash
python3 scripts/bench/mechanism_plan.py --experiment scaling-cruby \
  --execute-cell n3-app-sliced --output "$RUN_ROOT/scaling-cruby-n3" \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE" \
  > "$RUN_ROOT/scaling-cruby-n3.log" 2>&1
```

同様に`n20-app-sliced`、`n1000-app-sliced`を個別の新出力先で実行する。runnerは各セルで同じfixture・設定のpreflightを自動実施するため、古いpreflightファイルを渡さない。primary routeが不適格なtargetは性能評価へ採用しない。終了0だけでなく全trial、fixture hash/件数、応答照合、実際のk6設定を確認する。

FD試験では最初にdefaultを観測する。

```bash
python3 scripts/bench/mechanism_plan.py --experiment spinel-fds \
  --execute-cell pool10-nofiledefault --output "$RUN_ROOT/fd-pool10-default" \
  --remote-loadgen "$TESTER" --target-host "$APP_IP" \
  --gce-project "$PROJECT" --gce-zone "$ZONE" \
  > "$RUN_ROOT/fd-pool10-default.log" 2>&1
```

`process-start.json`のPID 1のsoft/hard nofileを確認する。default softが8192以上なら、そのままdefault/8192を比較しない。必要なら`--raised-nofile`を実測softより十分大きい整数へ変更する。defaultがunlimitedなど上限仮説に整合しなければ、errno診断を先に行う。

8192が有効な対照なら、残りは`pool512-nofiledefault`、`pool10-nofile8192`、`pool512-nofile8192`。各セルは新出力先で実施。上限はDockerの`--ulimit nofile=N:N`でcontainer起動時に設定され、PID 1の実測値と一致しなければ準備が失敗する。別のexec shellで`ulimit`を変更しただけでは対照にならない。

段階2は`--experiment pagination-cruby`、セルは`n1000-app-sliced`と`n1000-db-paged`。同じfixture・生成物で、返す20記事とコメント・順序を照合し、両方を新しく測定する。1Aの片側値を異なる日時・負荷履歴の対照として流用しない。必要時にruntime名を`jruby`/`spinel`へ変更する。100件を追加する場合は別profileの`fixture_articles`/workload名を変更し、`run.py run --profile PROFILE --dry-run`で検証してから実施する。

## 6. 観測と判定

| 問い | 必要な証拠 | 支持する結果 | 残る限界 |
| --- | --- | --- | --- |
| 件数で逆転するか | 実生成コード、同10 RPSの反復、CPU/latency | 表示20件固定の20→1,000件でemitのコストが大きく増える | 少数点だけで計算量は確定しない |
| DBページングが効くか | 応答同等性、SQL/取得行数、反復 | 同じ20件を返し、取得量とCPU/latencyが減る | モデル生成と関連付けが同時に変わる |
| FD上限が障害原因か | actual limits、FD数・最大番号・種別、TCP、server log | defaultで上限付近に達し、raisedで失敗が消える | errno未取得ならEMFILEは未確定 |
| 二重ループが原因か | 同行数/同出力の元版とハッシュ版、stage/CPU profile | 関連付け時間と全体性能が改善する | 取得量も変更すると単独効果にならない |

FD観測は`process-start.json`と、measurementの`fd-observations.json`にPID 1のlimits、FD count/max/type、重複socket FD、時刻、取得エラーを保存する。TCPも同時保存する。snapshotは非原子的で、重複socket FDはdupの呼出回数や割当元を示さない。観測失敗をFD 0としない。この機構はdup/acceptのerrnoを取得しない。

errnoが必要なら、同条件の別instrumented cohortでsyscall trace等を準備し、`dup/dup2/dup3/accept/accept4/close`の結果と時刻を取得する。必要な権限・記録取得を確認し、未取得なら原因を未確定のまま残す。上限引き上げによる成功だけで例外時のsocket解放も直ったとは認定しない。

YJIT/GC診断とJRubyのJFRは既存機構を使う。baselineとは別cohortで同fixture/route/10 RPSのprofileへ`diagnostics=true`、JRubyだけ`jfr=true`を設定する。warmup後/測定後のカウンター差分と観測時刻を保存し、割当/GC/JIT countは成功要求数で正規化する。`ratio_in_yjit`はbytecodeの実行割合でCPU時間の割合ではない。JFRは実ファイルとイベント収録を確認する。

SQL、モデル生成、関連付け、描画のstage timerとSpinelのCPU/worker待ちprofileは専用hooks未実装。原因が残る場合に追加し、実取得まで計測済みとは書かない。GC/JIT snapshotだけで支配的stageを断定しない。常駐量・GC寄与を論じるなら、container peakとは別に同条件のRSS/heap/割当/GC時間を取得する。

## 7. 改修対照・容量・提出

実生成コードで二重ループを確認した場合に限り、対象の関連付けをハッシュへ変えた版を用意する。ページングや取得対象削減を同時に含めない。元版/改修版は別の不変SHA・imageとして保存し、それぞれ新しいpreflightを実施する。微小ベンチに加え、同10 RPSのアプリ全体を各3反復して出力・DB効果とCPU/latency/memoryを比較する。

件数・ページング効果とFD対策の確認後、採用するworkload/接続policyを固定して新capacity cohortを組む。正式capacityは各5反復、既存SLOと120秒確認を維持する。新policyで改善しても旧失敗を成功へ書き換えない。

提出物：source/profile/image/fixture hash、生成コード確認、計画、全trial/warmup/measurement、k6 summary/invocation、CPU/memory/TCP/FD、server/build logs、trace取得状況、終了code。`mechanism-plan.json`/`mechanism-result.json`を保持し、未選択セルと実行中断を区別する。

全rawを回収・hash検証後、失敗時も両VMを停止する。timestamp付きTERMINATEDと`cleanup.json`、受信manifest、停止証跡を含む最終manifestを保存する。最後にrelease URL、archive SHA-256、source SHA、実施/未実施セル、全反復、支持された仮説と未確定事項を提出する。
