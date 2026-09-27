# Rails / Roundhouse / Spinel ベンチマーク：残作業と実施順

更新日：2026-09-27。対象は親 Issue [#11](https://github.com/koduki/example-rails-aot/issues/11) のうち、P0 の後に残る [#16〜#21](https://github.com/koduki/example-rails-aot/issues/16)。P0 実装（#12〜#15）は [PR #22](https://github.com/koduki/example-rails-aot/pull/22)、#16 は [PR #23](https://github.com/koduki/example-rails-aot/pull/23)、#17 は [PR #24](https://github.com/koduki/example-rails-aot/pull/24)、#18 は [PR #25](https://github.com/koduki/example-rails-aot/pull/25) で main にマージ完了。現在は P1 の #19（GCE 移行可能な実行契約・full 設定・再現手順）を実装中。

## 現在地と結果に付ける条件

[P0 の Actions 実行](https://github.com/koduki/example-rails-aot/actions/runs/36283048909)では、主 7 構成と JRuby JIT OFF の診断 2 構成をビルド・起動し、実プロセスの JIT/JVM JIT と SQLite 設定を検証した。既存の [AOT パイプライン](https://github.com/koduki/example-rails-aot/actions/runs/36283048890) も成功した。固定 fixture 上の `GET /articles`、`/articles/1`、`/articles/new`、`/articles.json`、`/articles/1.json` は全 9 構成で Rails 基準と一致した。通常の作成・更新・コメント操作・削除とその DB 効果も一致した。Rails の CRuby/JRuby は無効入力と CSRF を含めて全事前確認を通過している。

変換後の 5 構成には次の不一致が残る。性能比較の対象にするのは、事前確認で **個別に passed となった endpoint のみ**。全アプリの機能同等性は主張しない。

| 操作 | 現象 | 現時点の扱い |
| --- | --- | --- |
| HTML の無効な作成・更新 | フィールドのエラー表示用 CSS 属性などが Rails と異なる | 操作ケースを failed として保存。読み取り 5 経路は独立に eligible |
| JSON の無効な作成 | Ruby/JRuby 出力はエラー配列、Spinel は HTML 応答。Rails は項目別 JSON | failed。JSON 読み取りの一致と混同しない |
| 不正な CSRF token による作成 | 変換後は拒否せず書き込みを受け付ける | failed。書き込み負荷に進む前に修正・再検証が必要 |

SQLite の実版は CRuby 3.53.2、JRuby JDBC 3.46.1、Spinel 3.45.1。接続後の pragma は一致するが、この版差、HTTP サーバー、DB adapter、コード生成も Spinel との実行構成差に含まれる。現行の Python 負荷ドライバーは **closed loop のオーケストレーション検証用**であり、まだ容量順位や JIT 効果を発表する測定値はない。Actions の同じ実行に生ログと事前比較 artifact がある。

## 実施順と完了条件

| 順 | Issue | 優先度・難易度 | 実施する成果物と判定 |
| --- | --- | --- | --- |
| 1 | [#16 負荷・資源計測・対比較レポート](https://github.com/koduki/example-rails-aot/issues/16) | P1・高 | k6 の open arrival rate、共通 offered RPS と容量探索、CPU/RSS/cgroup 計測、raw から再生成できるレポート。client 飽和・不安定・失敗を順位から除外する |
| 2 | [#17 Actions の quick 測定](https://github.com/koduki/example-rails-aot/issues/17) | P1・中 | PR は smoke、手動実行は quick。同一 measurement job で全対象を逐次測定し、条件と raw artifact を保存する。手動 run URL を提示する |
| 3 | [#18 JIT 診断](https://github.com/koduki/example-rails-aot/issues/18) | P1・高 | JRuby compile.mode ON/OFF の 2×2 と YJIT の統計・ウォームアップ推移を、通常測定とは別 run で取得する |
| 4 | [#19 GCE 移行可能な実行契約](https://github.com/koduki/example-rails-aot/issues/19) | P1・中 | full profile、CPU/メモリ/負荷生成側の配置例、image・artifact の持ち込みと再集計手順。GCE の作成・実測は行わない |
| 5 | [#20 初回比較と原因調査・再測定](https://github.com/koduki/example-rails-aot/issues/20) | P1・中〜高 | 固定した quick 条件で有効な組を測り、Roundhouse の倍率、JIT との相互作用、Spinel の構成差を raw artifact とともに公開する |
| 6 | [#21 4 コア・大きい fixture・CRUD 等](https://github.com/koduki/example-rails-aot/issues/21) | P2・高 | 主結果が成立した後の補助評価。4 コアの資源がなければ profile と未実施理由を残す |

#16 は #15 の CLI と事前確認を土台に最初に実装する。#17 と #18 は #16 の測定・出力契約が決まれば並行して進められる。#19 の移行手順は #17 の Actions 統合結果を用いて検証する。#20 は #17〜#19 の後に行い、#21 は主比較の解釈を固めてから実施する。

## 1. 測定器を先に確定する：#16

- `bench/k6/read.js` は 1 iteration＝1 HTTP request、keep-alive、timeout、redirect、headers と成功判定を固定する。HTML と JSON は同じ小規模 fixture の読み取りを測る。負荷生成器はアプリと別 CPU に置く。
- 予備測定で各 endpoint の負荷範囲を調べ、共通 offered RPS での p50/p95/p99・成功率と、段階的な容量探索を別々に出す。容量判定の p99・失敗率閾値は予備測定後に設定ファイルへ commit し、本測定中には動かさない。Issue 記載の p99≤100 ms、予期しない失敗<0.1% は暫定案。
- started、completed、successful、timeout、check failure、`dropped_iterations` を別々に保存する。投入量を保てなかったクライアントは client saturation として無効にする。負荷生成側の CPU・VU の余裕も記録する。
- サーバーのプロセス群 CPU/RSS、コンテナの memory.current/peak・OOM・throttling を時系列で取得する。取得不能は `null` と理由にし、RSS とコンテナ総メモリを同じ数値として扱わない。
- `report.py` は raw artifact のみから JSON/CSV/Markdown と図を再生成する。各反復の値と中央値・範囲・ばらつきを示し、反復ごとの p99 の中央値を「全リクエストの p99」と表記しない。固定データで倍率計算を検証し、短い実 HTTP smoke で負荷生成を確認する。

主要な対比較は同じ workload・資源・判定基準で組む。SLO を満たす**容量指標**について、各 runtime の Roundhouse 効果は `capacity_emitted / capacity_Rails`、YJIT の倍率は `G = capacity_ON / capacity_OFF`、相互作用は `G_emitted / G_Rails` とする。同じ offered RPS での遅延・資源消費は別の表で比較する。その条件で成功 RPS が等しいことを性能同等の証拠にしない。JRuby は JIT ON/OFF の同じ構造で診断する。対応する組が欠損・未安定・不一致なら倍率も欠損とし、ゼロを代入しない。Spinel 比較は AOT 単独効果ではなく実行構成全体の差として扱う。

## 2. 同一 Actions job で quick を実行する：#17

既存 `benchmark-p0.yml` は全 9 構成の build/preflight まで成功している。次に #16 の k6/collect/report を共通 CLI へ接続し、PR では短時間 smoke、`workflow_dispatch` では quick を実行する。主 7 構成は同一 runner の **一つの測定 job** で全ビルド後に順序を入れ替えて逐次実行する。別 job や別 runner の RPS は対比較しない。

workflow input は profile・target・seed。artifact 名に commit・profile・run ID を含め、plan/env、起動時 JIT 状態、preflight、各反復の warmup と負荷 raw、資源時系列、report、失敗ログを常に保存する。hosted runner の実 CPU topology と load generator の余力を検査する。4 コアなど条件を満たさない測定は理由付き skip とし、warmup を勝手に短縮しない。既存 `aot.yml` の機能検証は継続する。

## 3. 効果の原因を診断する：#18

主 7 構成と別に JRuby compile.mode=OFF の 2 構成を測る。両方で JVM JIT は ON とし、JDK、heap/GC、CPU、DB、server の条件を変えない。通常測定とは別の診断 run で JRuby コンパイル/JVM compilation/GC ログ、YJIT の利用可能な stats・GC/allocations、warmup 中の throughput・遅延・CPU・メモリ推移を取得する。ログを有効にした run の速度を通常順位へ混ぜない。

解釈は「観測した倍率」「その説明を支えるログ」「なお未検証の機構」を区別する。YJIT/JRuby のコンパイルを OFF にしたときに Roundhouse の効果がどう変わるかを示すが、処理量削減と JIT の寄与を完全に分離できたとは断定しない。

## 4. GCE への持ち出しを可能にする：#19

Linux x86-64 + Docker の同じ CLI、コンテナ、結果 schema を維持する。GitHub 固有環境変数を外した環境で build/preflight/短時間 run/report を確認し、`quick` と `full` が同じ実行系を通るようにする。アプリと負荷生成器を別ホストに置く場合の接続先、CPU affinity、メモリ、出力先を設定化し、固定 image archive/digest・source・fixture とともに再集計手順を文書化する。現在の CLI は負荷生成器を同一ホスト上の別 CPU に置くため、別ホスト接続は未実装。

将来の GCE 実行では CPU 世代、vCPU/SMT topology、専有性、OS/カーネル、disk/network、background load、クライアント余力を記録する。full の所要時間は「対象×endpoint×反復×（warmup 上限＋測定時間＋起動余裕）」から上限を見積もり、予算不足は限定実行として明示する。**GCE の VM 作成や専用機での測定はこの Issue に含めない。**

## 5. 初回結果を出して再測定する：#20

予備測定で閾値・warmup・反復数・投入負荷を確定して commit した後、同じ Actions job で quick の主比較と JRuby OFF 診断を実行する。各表の値から run/artifact/commit/profile と raw の反復に辿れるようにする。差がばらつきと区別できなければ「判断保留」にする。

不一致や計測不具合を見つけた場合は最小再現を取り、アプリ／変換出力／runtime／測定器のどこが原因か分類し、修正、機能再確認、影響する対比較の**両側**再測定を行う。toolchain や設定が変わった前後の良い値だけを混ぜない。上流の制約で直せない場合は再現手順・ログ・除外範囲を明示する。特に CSRF と JSON 無効書き込みは未解決であり、#21 の CRUD 負荷の前提を満たしていない。

`docs/benchmark-results.md` には Roundhouse の有無、各 JIT の倍率と相互作用、Spinel の相対位置を分けて記述する。小規模・1 CPU・SQLite 読み取りの結果を Rails 全般へ外挿せず、GCE 専用環境で確認する仮説を残す。

## 6. 補助評価：#21

1 CPU の主比較が成立した後に 4 CPU、大きいデータ、90% 読み取り／10% 更新の初期 mix、独立 CRUD、起動時間、変換・コンパイルの時間/メモリ/サイズを追加する。大きい fixture は 1,000 記事＋コメントと共通の 20 件ページングを用意し、全対象で再度 preflight を通す。並行更新の対象を VU ごとに分け、競合や SQLite lock は独立 scenario とする。

4 アプリ CPU に加えて負荷生成側の余力を確保できない hosted runner では測定を実施せず、profile と必要な資源、未実施理由を残す。1 CPU と 4 CPU、定常と起動、読み取りと CRUD、warm/cold build はそれぞれ別表にする。

## 完了判定

P1 の完了は、同一環境の有効な HTML/JSON 対比較からレポートを再生成でき、実測 JIT 状態・機能一致・安定性・負荷生成器の余力を各値に結び付けられること。残る機能差や未実施条件は結果表に明記する。#21 は主比較を歪めず追加できた範囲だけを完了とし、専用 GCE の本測定は次の段階として残す。
