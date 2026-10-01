# GCE C3 再テスト分析報告書

分析日：2026-10-02（日本時間）

対象：`c3-retest-20261001T061200Z`、1,000記事・page 1の20記事

問い：改訂した容量探索と証跡保存は実機で機能したか。Rails／emit、JIT、Spinelの性能について何を確定できるか。

本文の主要比較はCRuby Off／YJIT、JRuby JIT有効、Spinelの7構成とする。JRuby Off二構成は補助診断として付録に残し、実施結果・失敗・未実行を省略しない。JRuby本体の評価にはJIT有効のRails／emitを使用する。

## 1. 対象と測定の境界

- [再テストrelease](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-retest-20261001-c3-retest-20261001T061200Z)
- [raw archive](https://github.com/koduki/example-rails-aot/releases/download/gce-c3-retest-20261001-c3-retest-20261001T061200Z/c3-retest-20261001T061200Z.tar.gz)：14,833,905 bytes
- 固定commit：[`4ae7f78111b1bdab07f455f793615e039018a276`](https://github.com/koduki/example-rails-aot/commit/4ae7f78111b1bdab07f455f793615e039018a276)。各runの`env.json`に同じSHA、`git_status:""`を確認。
- archive SHA-256：`86df7f78755c2259fdf3e5210458b35717f46e52489738f8450857129ef6fced`。公開値と一致。
- 正式profile SHA-256：`783b54f45834f89c23809b19829a68dbdc7b602eb3f992f28ce4f1e3c4e81a72`。保存profileの実バイトから再計算し`formal/env.json`と一致。

以下の証跡パスはすべてarchive内の`c3-retest-20261001T061200Z/`からの相対パス。元データを根拠とし、既存の生成レポートとrelease本文の説明は照合対象とした。

| 条件 | 今回の実値・根拠 |
| --- | --- |
| 配置 | release記載はc3-standard-4 × 2、asia-northeast1-b。envでApp／testerの別ホスト、各4論理CPU、2 core × SMT 2、Xeon Platinum 8481Cを確認 |
| 通信 | tester → private IP:3000。`formal/env.json`のICMP RTT平均0.039 ms、packet loss 0% |
| CPU／メモリ | App cpuset 0–3、container 14,336 MiB。4 vCPUを4物理coreとは呼ばない |
| fixture | 開始した正式30試行・診断60試行は各1,000記事。正式launchのfixture hashは全30件同じ。page responseはpreflightで一致 |
| workload | `/articles?page=1`、`app-sliced-page20-1000`。DB-paged性能やCRUD性能の測定ではない |
| CRuby | Ruby 3.4.5、YJIT enabled false／true、SQLite 3.53.2 |
| JRuby有効 | JRuby 10.0.7.0、compile_mode JIT、Java 21.0.12.1、HotSpot Tiered Compilers、SQLite 3.46.1 |
| Spinel | runtime spinel／jit aot、4 workers、SQLite 3.45.1。HTTP・DB adapter・runtimeを含むstack全体の比較 |
| 正式warmup | closed loop 4 VU、30秒窓、180–900秒、連続4窓、CV／drift 0.08 |
| 探索／確認 | 短時間30秒、confirm 120秒。区間幅 ≤ min(5 RPS, lower × 0.05)。最小設定1、最大12,800 RPS |
| 正式SLO | p99 ≤ 100 ms、failed/total < 0.001、drops=0、client saturationなし、tester CPU <85%・network errors=0 |
| 診断 | warmup 1 VU、measurement 120秒、各セル3反復、fresh container、diagnostics=false。1/4 VUはclosed loop、1/5/10 RPSはopen arrival |
| 負荷設定差 | 正式open arrivalはpreAllocatedVUs=512／maxVUs=4096、診断open arrivalは10／50。k6はv2.3.0 |
| 予算 | 正式total_timeout=36,000秒。正式計測はJST 10/1 20:01:07頃〜10/2 06:01:28頃（trial CPU開始端点から最後の終了記録） |

構成・runtimeの根拠は`formal/plan.json`、`formal/env.json`、各trialの`launch.json`／`container.json`、`preflight/<target>/runtime.json`。VM machine type・外部IPなしはreleaseの記載で、独立したgcloud describe出力は本archiveでは確認していない。

Rails／emitは同じruntime内での実装形状の比較、Off／YJITはCRuby内の設定比較として読む。JRubyとCRuby、Spinelの横断比較はDB libraryやserver構成も変わるため、コンパイラ単独の効果へ帰属させない。内部処理段階のCPU時間や待ち時間は未計測。

## 2. 指示書との照合と証跡の健全性

| 確認事項 | 結果・解釈 |
| --- | --- |
| primary correctness | 全9構成で`/articles?page=1`がeligibleかつpassed。各captureを保存。ただし全構成`complete:false` |
| CRUDの除外／不一致 | CSRF reference自身の不成立によるexclusion、invalid input応答の不一致が残る。primary page合格をCRUD全面合格とは扱わない |
| build／preflight | 終了コードとも0。buildの各image log、image IDs、preflight manifestあり |
| 診断coverage | VU 24/24、rate 36/36。全60件でindexとtrial、measurementの主要metricsを照合 |
| 正式coverage | planとindexの45件は対象・endpoint・repetitionが一致。開始30件のtrial.jsonあり、未開始15件はnot_run |
| 生metricsとの一致 | 正式213 probeでembedded measurementとk6-summaryのlatency・total・failed・failure_countsを照合。19合格すべて選択measurementが最後の有効120秒confirmと一致 |
| 正式App健全性 | 213 probeすべてApp sampleあり、throttled_periods_delta=0、OOM false。合格19件も独立に確認 |
| tester | 正式probe CPU 0.67–13.55%、network errors=0。合格confirmは1.48–9.20%。CPU／networkからのtester失格は確認されない |
| failure category | 正式213 probeの失敗request合計35,407件はすべてschema-v2 timeout。network／HTTP status／response integrityの失敗数は0 |
| 転送／manifest | 受信manifest 10,344件、最終manifest 10,348件をそれぞれ再検証し不一致0。転送検証exit=0 |
| 停止 | cleanup.jsonの両VMがstopped:true／TERMINATED。停止要求JST 10/2 06:08:58頃、確認06:10:50頃 |
| 未採取 | JFR／stack samples／request stage timing／queue gaugeなし。独立した同負荷3反復比較・追加境界trace cohortなし |
| 実機単体テスト記録 | 今回archiveにunittest実行ログは見当たらない。測定証跡とは別の不足として扱う |

段階終了コードはbuild=0、preflight=0、diag-vus=0、diag-rates=1、formal=1。diag-ratesの失敗6件と正式の失敗・未実行は記録されており、exit=1を転送障害と読み替えない。`logs/formal.log`など一部の統括ログは空だが、trial／phaseの証跡は残っている。診断のexit=0やtrial `passed`はp99 SLOの合格を保証しない。

正式45件の内訳はpassed 19、failed 11、not_run 15。failedの内訳は回復判定不成立9（JRuby Off二構成7、Spinel2）、境界再確認失敗1（Rails YJIT rep 3）、残り予算不足1（Rails JRuby rep 4）。**warmup未収束11件ではない**。主要7構成だけでは計35件中、passed 19、failed 4、not_run 12。

## 3. 正式容量の結果：主要7構成

数値は最終120秒confirmのsuccessful achieved RPS。中央値は成功した反復だけのpartial medianであり、全5反復の推定値や完全ランキングではない。未実行・失敗を0 RPSへ補完していない。

| 構成 | rep 1 | rep 2 | rep 3 | rep 4 | rep 5 | 確認数 | partial median RPS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rails-cruby-off | 57.80 | 48.42 | 60.92 | 未実行 | 未実行 | 3/5 | 57.80 |
| rails-cruby-yjit | 93.71 | 93.75 | 境界再確認失敗 | 未実行 | 未実行 | 2/5 | 93.73 |
| emit-cruby-off | 21.87 | 23.43 | 22.65 | 未実行 | 未実行 | 3/5 | 22.65 |
| emit-cruby-yjit | 108.96 | 106.21 | 80.85 | 未実行 | 未実行 | 3/5 | 106.21 |
| rails-jruby | 54.49 | 49.99 | 42.19 | 予算終了 | 未実行 | 3/5 | 49.99 |
| emit-jruby | 48.43 | 48.43 | 35.93 | 45.31 | 未実行 | 4/5 | 46.87 |
| spinel | 回復判定失敗 | 回復判定失敗 | 14.46 | 未実行 | 未実行 | 1/5 | 14.46 |

emit-CRuby Offは21.87–23.43 RPSで3回確認でき、旧テストで未探索だった25 RPS未満に有効な点が見つかった。emit-YJITは3反復目が80.85 RPSで、最初の2回の106–109 RPSとは差がある。Rails YJITはrep 3で87.891 offered RPSの確認に一度通ったが、再確認p99=102.81 msで失敗したため容量値を採用しない。

根拠：`formal/trials/per-run.json`、各`capacity-search.json`と最後の`*-confirm-*/k6-summary.json`。容量区間の全19件は付録Bに示す。16件の失敗上限は30秒、3件は120秒であり、全区間の両端を120秒で確定したとは書けない。

### 同一反復の倍率

式は各repの「分子のsuccessful achieved RPS ÷ 分母のsuccessful achieved RPS」。両側が有効confirmに通ったrepのみを採用し、その比率の中央値を示す。

| 分子／分母 | pair数 | 各repの比率 | 中央値 | 範囲 |
| --- | --- | --- | --- | --- |
| emit-cruby-off/rails-cruby-off | 3 | r1=0.378, r2=0.484, r3=0.372 | 0.378 | 0.372–0.484 |
| emit-cruby-yjit/rails-cruby-yjit | 2 | r1=1.163, r2=1.133 | 1.148 | 1.133–1.163 |
| emit-jruby/rails-jruby | 3 | r1=0.889, r2=0.969, r3=0.852 | 0.889 | 0.852–0.969 |
| rails-cruby-yjit/rails-cruby-off | 2 | r1=1.621, r2=1.936 | 1.779 | 1.621–1.936 |
| emit-cruby-yjit/emit-cruby-off | 3 | r1=4.982, r2=4.533, r3=3.569 | 4.533 | 3.569–4.982 |

今回の有効なpairでは、emit／RailsはCRuby Offで0.378倍、YJITで1.148倍、JRuby有効で0.889倍。JRuby有効では3 pairすべてemitがRailsを下回った。JRuby自体の測定は成立しており、JRuby Offを主要比較に必須とする理由はない。一方、全5反復が揃っていないため、一般的な優劣や安定した改善率として結論しない。

CRubyの2×2 interactionは、4セルがすべて合格したrep 1／2だけで計算できる。

`I = (emit-YJIT / emit-Off) / (Rails-YJIT / Rails-Off)`

rep 1 = 3.073、rep 2 = 2.341、中央値 = 2.707（n=2/5）。この2 pairではYJITを有効にしたとき、emit／Railsの相対比率が上がった。rep 3のRails YJITは失敗したため含めない。emitのどの処理段階がYJITに適したかは未計測。

## 4. SpinelとJRuby有効の低負荷診断

下表のRPSは各セル3反復のsuccessful RPS中央値、p99は各trial p99の最小–最大。全requestを統合したpercentileではない。closed-loopのRPSをoffered rateや最大容量とは呼ばない。

| 構成 | 負荷 | 中央値 RPS | p99範囲 ms | failed／drops（全3試行） | latency等のSLO条件適合数/3 |
| --- | --- | --- | --- | --- | --- |
| emit-jruby | 1 VU closed | 30.34 | 34.89–45.96 | 0 / 0 | 3 |
| spinel | 1 VU closed | 53.63 | 21.82–22.07 | 0 / 0 | 3 |
| emit-jruby | 4 VU closed | 63.62 | 100.26–115.26 | 0 / 0 | 0 |
| spinel | 4 VU closed | 137.14 | 37.96–38.06 | 0 / 0 | 3 |
| emit-jruby | 1 RPS open | 1.01 | 35.97–43.73 | 0 / 0 | 3 |
| spinel | 1 RPS open | 1.01 | 22.61–24.45 | 0 / 0 | 3 |
| emit-jruby | 5 RPS open | 5.01 | 36.73–39.51 | 0 / 0 | 3 |
| spinel | 5 RPS open | 5.01 | 26.89–27.83 | 0 / 0 | 3 |
| emit-jruby | 10 RPS open | 10.01 | 34.46–37.07 | 0 / 0 | 3 |
| spinel | 10 RPS open | 10.00 | 27.31–27.50 | 0 / 0 | 3 |

emit-JRubyは1 VUでは低遅延だが、4 VUはエラーゼロでもp99=100.26–115.26 msで全3件が100 ms条件を超える。1/5/10 RPSのopen arrivalは全9件でSLO条件に適合した。

Spinelは1 VUで約53.6 RPS、4 VUで約137.1 RPS、p99約38 ms・エラーゼロ。1/5/10 RPS openも全9件で適合した。しかし正式探索はrep 1／2が途中で回復判定不成立、rep 3だけ14.453 offered RPSで120秒合格（achieved 14.458 RPS、p99 64.86 ms）。

これは「Spinelは低負荷でも一律に動作しない」という説明を反証する。一方、4 VUで137 RPS出たことからopen arrivalでも137 RPSを達成できるとは言えない。負荷方式だけでなく、warmup 1／4 VU、VU pool設定10/50／512/4096、探索中の過負荷履歴も変わっている。

### Spinelの失敗履歴

- `formal/trials/0008-spinel/`（rep 1）：100／50／25 RPS、30秒でtimeout。12.5 RPS短時間は合格。15.625 RPSは30秒に通ったが120秒では414/1,875 timeout。7.812 RPSの120秒確認は合格後、11.719 RPSで1,036/1,407 timeout。その後の1 RPS healthもtimeoutし試行終了。
- `formal/trials/0024-spinel/`（rep 3）：100／50／25 RPSでtimeout、12.5 RPSから低負荷側を探索。14.843 RPS、30秒は2/446 timeoutで失敗。14.453 RPS、120秒はエラーゼロで合格。失敗上限のp99は58.96 msだがerror rate 0.448%でSLO不合格であり、p99だけでは失敗を見落とす。
- 同rep 3のopen arrivalでは、100 RPS時の観測VU peak=116、25 RPS時=62、14.453 RPS時=1。closed-loop 4 VUとはclient／connectionの利用形態が異なる可能性がある。ただしこの値はqueue depthやserver worker occupancyではなく、原因の証明にも使わない。

client CPU不足、App OOM、CPU throttlingは今回の記録では説明にならない。HTTP keep-alive／connection分配、worker scheduling、DB待ち、過負荷後の残存処理などを切り分けるserver traceは未採取。release本文の「queue timeout」は、現時点では**clientの5秒request timeoutが観測された**という表現までに限定する。

## 5. 容量探索・回復・予算の実機検証

213 probe、100 recovery記録（healthy_probe 91、unrecovered 9）を確認。旧探索の単純な半減終了から改善した挙動が実機で残っている。

| 実装上の確認点 | 実機で確認できたこと／限界 |
| --- | --- |
| 確認失敗後の上向きrefinement | Rails JRuby rep 1、emit-YJIT rep 1／3で120秒失敗→半減合格→上向き120秒探索→最終合格を確認 |
| 例：Rails JRuby rep 1 | 56.25 RPS confirm失敗後、28.125→42.188→49.219→52.734→54.492とconfirmで上昇。下限54.492、上限56.25（120秒）、幅1.758 ≤ tolerance 2.7246 |
| 境界再確認 | Rails YJIT rep 3では最後の再確認が失敗しboundary_unstable。過去に通った87.891 RPSを有効capacityへ昇格していない |
| 精度 | 合格19件はすべてinterval幅 ≤ tolerance。上限のprobe rate／duration／失敗と下限の120秒confirmを照合 |
| 失敗保存 | 開始した失敗11件にもtrial.jsonがあり、途中capacity stepsやrecoveryが保存されている |
| 1 RPSまでの容量探索 | 設定min=1は保存されているが、実際のcapacity probe最小は7.812 RPS。1 RPSまでの探索機能の実機確認は未達。1 RPS recoveryや診断は別用途 |
| 回復 | 成功healthでもserver_queue_drained:nullを保持。queue drainageの証明にはしていない |
| 予算 | 10時間終了後15 not_run。最後のRails JRuby rep 4はwarmup後に残りcapacity予算不足で終了 |

正式runのk6 elapsed合計はwarmup約2.56時間、capacity probes約2.98時間、recovery約0.17時間、合計約5.71時間。約10時間の経過時間との差には起動・remote制御・回収・telemetry等の処理が含まれる。これを単一のSSH overheadと断定しない。短時間measurementだけから所要時間を見積もると大きく不足する。

JRuby Off二構成の開始から終了までの記録区間は合計約1.20時間。主要7構成へ絞ることは時間削減に有効だが、それだけで残り反復が同じ予算内に完了する保証はない。

**回復判定には設計上の区別が必要。** `run.py:recover`は5秒の1 RPS probeにも正式容量のp99≤100 msを適用している。JRuby Offのunrecovered 7件、計21 health attemptsは全request成功・timeoutゼロなのにp99超過で終了した。これは低負荷の応答健全性と性能SLO不適合が混ざった状態で、queueが残った証拠ではない。今後は「応答・integrityは回復」「正式SLOは不適合」を別に記録できる設計が望ましい。正式capacityの閾値を緩める必要はない。

根拠実装：[capacity.py](https://github.com/koduki/example-rails-aot/blob/4ae7f78111b1bdab07f455f793615e039018a276/scripts/bench/capacity.py)、[run.py](https://github.com/koduki/example-rails-aot/blob/4ae7f78111b1bdab07f455f793615e039018a276/scripts/bench/run.py)、[k6 read.js](https://github.com/koduki/example-rails-aot/blob/4ae7f78111b1bdab07f455f793615e039018a276/bench/k6/read.js)。

## 6. 仮説と次に必要な最小実験

| 優先 | 論点 | 今回の証拠 | 判定／次の実験 |
| --- | --- | --- | --- |
| 1 | Spinelのopen arrival／connection処理 | 4 VU約137 RPSは正常、formal openは約14.45 RPSで1回確認。VU poolと履歴が異なる | 原因未確定。同じfresh container条件・warmupで10/15/25 RPS × preAllocatedVUs 10/512（max設定固定）をまず各3回。必要ならkeep-alive有無を別変数で比較。accept／dispatch／response、worker、active requestを同期収集 |
| 2 | 主要7構成の再現性 | どの構成も5/5なし、YJIT／JRubyにはrep差 | 主要7構成を新しいrun IDで反復。trialごとの実経過時間に基づく予算か、対象を分けたbalanced scheduleで5反復を確保。旧runのpartial値と黙って合成しない |
| 3 | emit-OffとYJITの差 | CRuby Offは約0.38倍、YJITは約1.15倍のemit/Rails比。interaction n=2 | 相対効果は今回のpairで支持、機序未確定。4構成を共通の健全rate（候補10 RPS）・120秒・3反復以上で比較し、SQL／materialization／render stageとCPU samplesを別計測 |
| 4 | JRuby有効のemit/Rails差 | 全3有効pairでemit < Rails、0.852–0.969倍 | JRubyを主要評価に残す。matched-rate・同warmup条件でCPU stacks／allocation／GCを採取。Offを原因説明の必須前提にしない |
| 補助 | healthと性能SLOの共用 | JRuby Offは1 RPSでtimeoutなし、p99超過でrecovery終了 | 回復healthとcapacity eligibilityを別状態にする改修候補。診断reportにもp99 SLO判定を独立表示 |

上記は次回実験・改修の提案であり、この分析ではGCE再実行やリポジトリ変更は行っていない。Spinel試験ではまずVU poolだけを変え、keep-aliveや負荷方式を同時に変えない。過負荷後の回復を確認する場合は同じcontainerで低→高→低を記録する独立試験が必要。fresh containerを毎回作るload_sweepのrateリストだけではこの目的を満たせない。

資源比較は今回のcapacity点ではoffered RPSが異なるため、CPU／メモリの効率順位を出さない。付録BのCPUはDocker telemetryの区間平均、100%=1論理CPU相当、remote orchestrationを含む。メモリはpeak container bytes / 2^20（MiB）でありprocess RSSではない。

## 7. 旧9/30結果との関係

旧runはcommit `bf098d3e724af5b84d26da9e8398942716a50009`、32 VU warmup、粗い探索・25 RPS未満未探索の別cohort。今回の4 VU warmupと確認後refinementを含む値を割り算し、runtime改善／退行の倍率へ変換しない。

旧emit-Offは25 RPSで不合格だったが、今回の約22 RPS確認はそれと両立する。旧SpinelやJRuby Offの大量warmup timeoutに対して、低VUで応答が成立する条件が得られた。旧JRuby有効のemit/Rails比が今回と違うことは、測定状態・warmup・探索精度が影響し得るため、原因切り分けが必要である。

## 付録A. JRuby Offの補助診断

Off二構成はJRubyのcompile_mode=OFFを指定した実験。runtime記録にはHotSpot Tiered Compilersが残っており、全JIT無効と同義ではない。主要な実運用設定の比較からは分離し、全試行のcoverageと負荷依存性を保持する。

| 構成 | rep 1 | rep 2 | rep 3 | rep 4 | rep 5 |
| --- | --- | --- | --- | --- | --- |
| rails-jruby-off | 回復判定失敗 | 回復判定失敗 | 回復判定失敗 | 回復判定失敗 | 未実行 |
| emit-jruby-off | 回復判定失敗 | 回復判定失敗 | 回復判定失敗 | 未実行 | 未実行 |

| 構成 | 負荷 | 中央値 RPS | p99範囲 ms | failed 各rep | drops 各rep | 正式SLO条件適合数/3 |
| --- | --- | --- | --- | --- | --- | --- |
| rails-jruby-off | 1 VU closed | 5.93 | 198.02–200.98 | 0/0/0 | 0/0/0 | 0 |
| emit-jruby-off | 1 VU closed | 1.88 | 534.92–567.15 | 0/0/0 | 0/0/0 | 0 |
| rails-jruby-off | 4 VU closed | 11.89 | 510.54–536.15 | 0/0/0 | 0/0/0 | 0 |
| emit-jruby-off | 4 VU closed | 3.00 | 1437.92–1461.91 | 0/0/0 | 0/0/0 | 0 |
| rails-jruby-off | 1 RPS open | 1.00 | 184.21–191.66 | 0/0/0 | 0/0/0 | 0 |
| emit-jruby-off | 1 RPS open | 1.00 | 531.52–538.13 | 0/0/0 | 0/0/0 | 0 |
| rails-jruby-off | 5 RPS open | 5.00 | 195.21–197.21 | 0/0/0 | 0/0/0 | 0 |
| emit-jruby-off | 5 RPS open | 0.37 | 5000.84–5000.86 | 543/539/538 | 16/16/16 | 0 |
| rails-jruby-off | 10 RPS open | 9.99 | 352.77–705.75 | 0/0/0 | 0/0/0 | 0 |
| emit-jruby-off | 10 RPS open | 0.18 | 5000.87–5000.88 | 1119/1121/1122 | 58/58/58 | 0 |

Rails JRuby Offは1/5/10 RPSでエラーゼロだがp99は184–706 msで、全9試行が100 ms条件に不適合。emit JRuby Offは1 RPSでエラーゼロ・p99約532–538 ms、5 RPSで約92% timeout、10 RPSで約98% timeout。5/10 RPSはそれぞれ各rep 16／58 dropsもある。SLO違反と未発行を併記し、正確な最大容量の上限として流用しない。

emit JRuby Offの「1 RPSが合格、5 RPSで失敗」というrelease説明は、応答エラーの有無としては再現したが、正式100 ms SLOでは1 RPSも不合格。1と5の間の正確なtimeout発生境界は未測定。原因のqueue／DB／CPU帰属も未確定。

## 付録B. 有効confirmのmetricsと探索区間（全19件）

L／Uはoffered RPSの確認下限／失敗上限。Lは120秒、Uの時間は個別表示。achieved RPSは本文表。全19件でfailed=0、drops=0、network errors=0、client_saturated=false、App sampleあり、throttle増加0、OOM false。

| 構成 | rep | L／U RPS | U秒 | 幅／許容 RPS | p50／p95／p99 ms | App CPU % | container peak MiB | tester CPU % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rails-cruby-off | 1 | 57.812 / 59.375 | 30 | 1.563 / 2.891 | 43.19 / 65.57 / 89.92 | 197.22 | 414.90 | 4.74 |
| rails-cruby-off | 2 | 48.438 / 50.000 | 30 | 1.562 / 2.422 | 35.72 / 72.23 / 88.05 | 150.35 | 356.00 | 3.95 |
| rails-cruby-off | 3 | 60.938 / 62.500 | 30 | 1.562 / 3.047 | 46.26 / 70.69 / 99.72 | 215.92 | 424.90 | 4.96 |
| rails-cruby-yjit | 1 | 93.750 / 96.875 | 30 | 3.125 / 4.688 | 23.04 / 80.04 / 98.45 | 178.57 | 472.50 | 7.57 |
| rails-cruby-yjit | 2 | 93.750 / 96.875 | 30 | 3.125 / 4.688 | 22.57 / 79.14 / 99.79 | 167.68 | 473.60 | 7.49 |
| emit-cruby-off | 1 | 21.875 / 22.657 | 30 | 0.782 / 1.094 | 70.54 / 83.22 / 91.54 | 131.89 | 207.20 | 2.09 |
| emit-cruby-off | 2 | 23.438 / 24.219 | 30 | 0.781 / 1.172 | 70.34 / 82.85 / 95.34 | 139.51 | 175.00 | 2.13 |
| emit-cruby-off | 3 | 22.657 / 23.438 | 30 | 0.781 / 1.133 | 70.53 / 82.73 / 91.91 | 134.90 | 174.40 | 2.14 |
| emit-cruby-yjit | 1 | 108.984 / 112.500 | 120 | 3.516 / 5.000 | 32.73 / 52.41 / 78.19 | 260.26 | 236.00 | 9.20 |
| emit-cruby-yjit | 2 | 106.250 / 109.375 | 30 | 3.125 / 5.000 | 32.00 / 52.91 / 76.56 | 245.71 | 224.00 | 8.85 |
| emit-cruby-yjit | 3 | 80.859 / 84.375 | 120 | 3.516 / 4.043 | 19.33 / 28.15 / 34.78 | 133.07 | 222.00 | 6.63 |
| rails-jruby | 1 | 54.492 / 56.250 | 120 | 1.758 / 2.725 | 26.72 / 58.17 / 96.41 | 185.68 | 1613.82 | 4.39 |
| rails-jruby | 2 | 50.000 / 51.562 | 30 | 1.562 / 2.500 | 25.99 / 55.67 / 67.50 | 165.47 | 1509.38 | 4.07 |
| rails-jruby | 3 | 42.188 / 43.750 | 30 | 1.562 / 2.109 | 25.04 / 51.42 / 66.04 | 134.08 | 1676.29 | 3.34 |
| emit-jruby | 1 | 48.438 / 50.000 | 30 | 1.562 / 2.422 | 30.36 / 62.08 / 76.65 | 148.56 | 939.10 | 4.10 |
| emit-jruby | 2 | 48.438 / 50.000 | 30 | 1.562 / 2.422 | 34.87 / 67.09 / 90.00 | 151.56 | 923.50 | 4.08 |
| emit-jruby | 3 | 35.938 / 37.500 | 30 | 1.562 / 1.797 | 31.98 / 46.83 / 67.33 | 113.00 | 982.60 | 3.13 |
| emit-jruby | 4 | 45.312 / 46.875 | 30 | 1.563 / 2.266 | 30.91 / 54.16 / 66.21 | 138.51 | 987.30 | 3.85 |
| spinel | 3 | 14.453 / 14.843 | 30 | 0.390 / 0.723 | 21.70 / 23.75 / 64.86 | 28.87 | 500.40 | 1.48 |

Rails YJIT rep 3はこの表から除外。`formal/trials/0020-rails-cruby-yjit/capacity-search.json`で87.891 RPSの120秒p99が93.50 ms→最終再確認102.81 msと変化している。過去の合格点は履歴として残るが、最終有効capacityではない。

## 付録C. 診断の全60反復と証跡

診断のrunner statusと正式SLO条件判定を分ける。`SLO条件適合`は当該120秒セルでの条件判定であり、closed loopの最大容量確認を意味しない。App CPU・メモリ・全phase telemetryは各cellのtrial／telemetry、tester記録はmeasurement directoryに保存されている。

| 構成 | 負荷 | rep | runner status | successful RPS | p99 ms | failed/total | drops | SLO条件 | tester CPU % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| emit-jruby | 1 VU | 1 | passed | 27.97 | 45.96 | 0/3357 | 0 | 適合 | 2.41 |
| emit-jruby | 1 VU | 2 | passed | 31.84 | 34.89 | 0/3822 | 0 | 適合 | 2.10 |
| emit-jruby | 1 VU | 3 | passed | 30.34 | 36.94 | 0/3641 | 0 | 適合 | 2.28 |
| spinel | 1 VU | 1 | passed | 53.70 | 21.82 | 0/6445 | 0 | 適合 | 3.08 |
| spinel | 1 VU | 2 | passed | 53.39 | 22.07 | 0/6408 | 0 | 適合 | 3.19 |
| spinel | 1 VU | 3 | passed | 53.63 | 21.94 | 0/6436 | 0 | 適合 | 3.65 |
| rails-jruby-off | 1 VU | 1 | passed | 6.15 | 200.98 | 0/739 | 0 | 不適合 | 1.05 |
| rails-jruby-off | 1 VU | 2 | passed | 5.88 | 198.23 | 0/706 | 0 | 不適合 | 0.55 |
| rails-jruby-off | 1 VU | 3 | passed | 5.93 | 198.02 | 0/712 | 0 | 不適合 | 0.47 |
| emit-jruby-off | 1 VU | 1 | passed | 1.92 | 534.92 | 0/231 | 0 | 不適合 | 0.23 |
| emit-jruby-off | 1 VU | 2 | passed | 1.85 | 554.18 | 0/222 | 0 | 不適合 | 0.23 |
| emit-jruby-off | 1 VU | 3 | passed | 1.88 | 567.15 | 0/227 | 0 | 不適合 | 0.77 |
| emit-jruby | 4 VU | 1 | passed | 61.86 | 114.76 | 0/7424 | 0 | 不適合 | 3.90 |
| emit-jruby | 4 VU | 2 | passed | 66.93 | 100.26 | 0/8035 | 0 | 不適合 | 4.35 |
| emit-jruby | 4 VU | 3 | passed | 63.62 | 115.26 | 0/7636 | 0 | 不適合 | 4.90 |
| spinel | 4 VU | 1 | passed | 137.28 | 37.96 | 0/16476 | 0 | 適合 | 8.46 |
| spinel | 4 VU | 2 | passed | 135.87 | 38.00 | 0/16307 | 0 | 適合 | 8.45 |
| spinel | 4 VU | 3 | passed | 137.14 | 38.06 | 0/16458 | 0 | 適合 | 8.31 |
| rails-jruby-off | 4 VU | 1 | passed | 11.82 | 510.54 | 0/1420 | 0 | 不適合 | 0.84 |
| rails-jruby-off | 4 VU | 2 | passed | 11.91 | 512.78 | 0/1431 | 0 | 不適合 | 0.91 |
| rails-jruby-off | 4 VU | 3 | passed | 11.89 | 536.15 | 0/1428 | 0 | 不適合 | 1.35 |
| emit-jruby-off | 4 VU | 1 | passed | 3.21 | 1437.92 | 0/388 | 0 | 不適合 | 0.32 |
| emit-jruby-off | 4 VU | 2 | passed | 3.00 | 1461.91 | 0/361 | 0 | 不適合 | 0.34 |
| emit-jruby-off | 4 VU | 3 | passed | 3.00 | 1459.08 | 0/362 | 0 | 不適合 | 0.32 |
| emit-jruby | 1 RPS | 1 | passed | 1.01 | 35.97 | 0/121 | 0 | 適合 | 0.23 |
| emit-jruby | 1 RPS | 2 | passed | 1.01 | 43.73 | 0/121 | 0 | 適合 | 0.67 |
| emit-jruby | 1 RPS | 3 | passed | 1.01 | 37.76 | 0/121 | 0 | 適合 | 0.15 |
| spinel | 1 RPS | 1 | passed | 1.00 | 24.45 | 0/120 | 0 | 適合 | 0.17 |
| spinel | 1 RPS | 2 | passed | 1.01 | 22.61 | 0/121 | 0 | 適合 | 0.15 |
| spinel | 1 RPS | 3 | passed | 1.01 | 24.37 | 0/121 | 0 | 適合 | 0.14 |
| rails-jruby-off | 1 RPS | 1 | passed | 1.00 | 184.77 | 0/120 | 0 | 不適合 | 0.17 |
| rails-jruby-off | 1 RPS | 2 | passed | 1.00 | 191.66 | 0/120 | 0 | 不適合 | 0.72 |
| rails-jruby-off | 1 RPS | 3 | passed | 1.01 | 184.21 | 0/121 | 0 | 不適合 | 0.15 |
| emit-jruby-off | 1 RPS | 1 | passed | 1.00 | 536.48 | 0/120 | 0 | 不適合 | 0.14 |
| emit-jruby-off | 1 RPS | 2 | passed | 1.00 | 538.13 | 0/121 | 0 | 不適合 | 0.15 |
| emit-jruby-off | 1 RPS | 3 | passed | 1.00 | 531.52 | 0/120 | 0 | 不適合 | 0.24 |
| emit-jruby | 5 RPS | 1 | passed | 5.01 | 39.51 | 0/601 | 0 | 適合 | 0.51 |
| emit-jruby | 5 RPS | 2 | passed | 5.01 | 38.48 | 0/601 | 0 | 適合 | 0.52 |
| emit-jruby | 5 RPS | 3 | passed | 5.00 | 36.73 | 0/600 | 0 | 適合 | 0.46 |
| spinel | 5 RPS | 1 | passed | 5.01 | 27.83 | 0/601 | 0 | 適合 | 0.98 |
| spinel | 5 RPS | 2 | passed | 5.01 | 27.49 | 0/601 | 0 | 適合 | 0.47 |
| spinel | 5 RPS | 3 | passed | 5.01 | 26.89 | 0/601 | 0 | 適合 | 0.46 |
| rails-jruby-off | 5 RPS | 1 | passed | 5.00 | 197.21 | 0/601 | 0 | 不適合 | 0.52 |
| rails-jruby-off | 5 RPS | 2 | passed | 5.00 | 195.88 | 0/601 | 0 | 不適合 | 0.48 |
| rails-jruby-off | 5 RPS | 3 | passed | 5.00 | 195.21 | 0/600 | 0 | 不適合 | 0.47 |
| emit-jruby-off | 5 RPS | 1 | failed | 0.34 | 5000.86 | 543/585 | 16 | 不適合 | 0.72 |
| emit-jruby-off | 5 RPS | 2 | failed | 0.37 | 5000.84 | 539/585 | 16 | 不適合 | 0.19 |
| emit-jruby-off | 5 RPS | 3 | failed | 0.38 | 5000.84 | 538/585 | 16 | 不適合 | 0.15 |
| emit-jruby | 10 RPS | 1 | passed | 10.01 | 37.07 | 0/1201 | 0 | 適合 | 1.54 |
| emit-jruby | 10 RPS | 2 | passed | 10.01 | 34.46 | 0/1201 | 0 | 適合 | 0.84 |
| emit-jruby | 10 RPS | 3 | passed | 10.01 | 36.95 | 0/1201 | 0 | 適合 | 0.81 |
| spinel | 10 RPS | 1 | passed | 10.01 | 27.31 | 0/1201 | 0 | 適合 | 0.88 |
| spinel | 10 RPS | 2 | passed | 10.00 | 27.37 | 0/1200 | 0 | 適合 | 0.85 |
| spinel | 10 RPS | 3 | passed | 10.00 | 27.50 | 0/1200 | 0 | 適合 | 0.83 |
| rails-jruby-off | 10 RPS | 1 | passed | 9.99 | 352.77 | 0/1200 | 0 | 不適合 | 0.84 |
| rails-jruby-off | 10 RPS | 2 | passed | 9.99 | 705.75 | 0/1201 | 0 | 不適合 | 1.29 |
| rails-jruby-off | 10 RPS | 3 | passed | 9.99 | 429.19 | 0/1201 | 0 | 不適合 | 0.81 |
| emit-jruby-off | 10 RPS | 1 | failed | 0.18 | 5000.88 | 1119/1142 | 58 | 不適合 | 0.24 |
| emit-jruby-off | 10 RPS | 2 | failed | 0.18 | 5000.87 | 1121/1143 | 58 | 不適合 | 0.24 |
| emit-jruby-off | 10 RPS | 3 | failed | 0.17 | 5000.88 | 1122/1143 | 58 | 不適合 | 0.30 |

証跡の対応：

- 正式：`formal/plan.json` → `formal/trials/per-run.json` → `formal/trials/<schedule index>-<target>/trial.json` → `capacity-search.json` → `<probe index>-<phase>-<rate>/k6-summary.json`・`k6.log`・`app-telemetry.json`。
- 診断：`diag-vus/000-vus-1`／`001-vus-4`、`diag-rates/000-rates-1`／`001-rates-5`／`002-rates-10`。各cellの`plan.json`、`trials/per-run.json`、各trialの`measurement/k6-summary.json`。
- 回復：正式各trialの`recovery-*/recovery.json`とattempt directory。
- 停止・転送：rootの`cleanup.json`、`transfer-verification.log`／`.exit`、`SHA256SUMS.received`、`SHA256SUMS`。

再計算は公開archiveをhash検証して展開し、固定commitの`gce-log-investigator/scripts/summarize.py`でformalを読み取る。`diagnostic.py`による再生成はworking copyに対して行う。全45件のindexと開始30件のtrialを照合し、各probeに`capacity.classify(measurement, formal plan profile)`を適用する。倍率は同じrepのpassed同士を割り、区間・120秒confirm・App／tester条件を確認した後に集約する。

## まとめ

再テストは証跡保存・容量refinement・失敗時保存・未実行の記録・両VM停止を実機で確認できた。受信・最終manifestは全件一致。正式容量は45件中19件確認、15件未実行で、完全ランキングは未成立。

主要比較ではemit／Railsのpaired partial medianがCRuby Off 0.378倍（n=3）、YJIT 1.148倍（n=2）、JRuby有効0.889倍（n=3）。emit-Offの25 RPS未満の容量点は新たに確認された。JRuby Offは付録の補助診断で十分で、JRuby有効の評価は本文に残せる。

Spinelは4 VU約137 RPS・エラーゼロという健全な条件がある一方、正式open arrivalでは約14.45 RPSを1回確認したのみ。この差をAOT性能、DB待ち、queue timeoutへ即座に帰属させず、VU pool／connection／workerと過負荷履歴を制御した次の実験を優先する。
