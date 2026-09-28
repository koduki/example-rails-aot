# Rails × Roundhouse × JIT / Spinel AOT 検証報告書

**対象:** 記事・コメントを扱う Rails 8.0.5.1 の小規模アプリ。2026-09-28 時点のコードと公開 CI 成果物に基づく。

## 1. 要約

Roundhouse が出力した Ruby は、測定した `GET /articles` で元の Rails より高い観測 RPS を示した。CRuby の短時間実験では、生成 Ruby／Rails は YJIT Off **7.489 倍**、On **5.611 倍**。別条件の JRuby 反復実験では、`compile.mode=OFF` **10.874 倍**、`JIT` **7.488 倍**（各構成の3反復中央値）である。

**JIT On によって性能が下がったわけではない。** 同じ実験内では Rails と生成 Ruby の絶対 RPS がどちらも増えた。縮んだのは生成 Ruby／Rails の**相対倍率**である。ただし JRuby の Rails/JIT On は反復間に **105.00 → 301.41 → 313.84 RPS** の段差があり、相互作用の大きさや原因は確定できない。

| 観点 | 観測と判定 |
| --- | --- |
| 機能 | 9構成で読み取り5経路が適格。正常系の read/update、create/delete は各9/9 `verified` |
| 処理量 | CRuby の1回10秒実験では Rails と生成 Ruby の両方で YJIT On が高い |
| JRuby | 3反復の中央値では Rails と生成 Ruby の両方で `compile.mode=JIT` が高い。Rails/JIT On の段差が大きい |
| Spinel | 4,516.54 RPS、最大コンテナ使用量 12.63 MB。VM・HTTP・DB を含む実行スタック全体の観測 |
| 適用範囲 | 小さな SQLite fixture と hosted runner の予備測定。最大持続容量や一般の Rails アプリへの効果は未確定 |

## 2. 対象アーキテクチャ

`blog/` が Rails **8.0.5.1** の正本である。CSRF 検証はこの実験アプリ全体で無効にしている。正常系の生成ランタイム比較のための条件であり、CSRF 保護を備えた本番アプリの安全性を示さない。

~~~mermaid
flowchart TD
    A["Rails 8.0.5.1: 記事・コメント"] --> B["Roundhouse v2026.9.18"]
    A --> C["測定用 Rails コピー"]
    B --> D["生成 Ruby: CRuby / JRuby"]
    B --> E["Spinel AOT: 専用 HTTP / DB"]
    C --> F["Rails: CRuby / JRuby"]
    D --> G["HTTP / DB 検証と測定"]
    E --> G
    F --> G
~~~

Roundhouse は Rails ソース、ERB、スキーマ、ルートを取り込み、解析と lower を経てターゲット別のコードを出力する。生成物にはターゲット別のランタイムが含まれ、Rails gem をそのまま動かす構成ではない。[固定版の構造と既知の警告](provenance.md#appendix-roundhouse-architecture-v2026918)を参照。測定用コピーは [prepare_app.py](../scripts/bench/prepare_app.py) が正本から作り、JRuby JDBC 用 lockfile、起動、DB、Puma の設定を適用する。差分は manifest に記録される。

| 比較 | 主な変更点 | 識別できる範囲 |
| --- | --- | --- |
| Rails ↔ 生成 Ruby、処理系と JIT 設定は固定 | コード形状、Rails と生成ランタイム | Roundhouse 経路**全体**の観測差 |
| CRuby YJIT Off ↔ On、アプリ形状は固定 | YJIT 設定 | 各形状における切替の観測差 |
| JRuby `compile.mode=OFF` ↔ `JIT`、形状は固定 | JRuby のコンパイルモード | JRuby の切替差。HotSpot の JVM JIT は両方で有効 |
| Rails/CRuby ↔ Spinel AOT | VM、生成コード、HTTP、DB アダプター | 実行スタック全体の差。AOT コンパイラ単独の効果には分解できない |

## 3. 仮説と検証設計

| 仮説 | 予測 | 今回の証拠 | 次に必要な観測 |
| --- | --- | --- | --- |
| H1: 生成コードはリクエスト時の Rails 抽象化を減らす | 同一処理系で CPU 時間や割当が減る | 限定 endpoint の生成 Ruby RPS は高い。機構自体は未測定 | CPU profile、割当/GC、SQL・ルーティング・描画時間 |
| H2: 生成コードは JIT の寄与を大きくする | 生成 Ruby の On/Off 改善比が Rails の改善比を上回る | CRuby と JRuby の集計比はいずれも逆方向。仮説の強い形は支持されない | 同一条件の反復と JIT コンパイル統計 |
| H3: JRuby のウォームアップ段階が倍率を左右する | 短い測定と長い反復で Rails/JIT On の基準値が変わる | 短時間 29.15 RPS、別実験の3反復 105.00/301.41/313.84 RPS。実験条件も異なる | 同じコミットでの長い連続時系列、コンパイルログ、GC、試行順の交差 |
| H4: Spinel の実行スタックは小さい | 同一 endpoint でコンテナ使用量が小さい | 短時間実験で 12.63 MB | 起動・アイドル・定常の反復と HTTP/DB 層を揃えた対照 |

RPS から Ruby CPU、SQLite/OS 時間、型ガード失敗、GC ポーズの内訳は逆算できない。これらを原因として定量化するには独立の計測が必要である。

## 4. 測定方法と正確性

| 実験 | ソースと成果物 | 対象・負荷 | 採用目的 |
| --- | --- | --- | --- |
| S: CRuby / Spinel 短時間 | [Actions 36380159185](https://github.com/koduki/example-rails-aot/actions/runs/36380159185)・[生データ](https://github.com/koduki/example-rails-aot/actions/runs/36380159185/artifacts/10953212083)、head `2b66fbb2c90752320926e2715a8c48c31bbd33c1` | 7構成を各1回、`GET /articles`、4接続・10秒 closed-loop。Rails 8.0.5.1 の正本と派生コピー | 正確性、RPS と応答時間の予備観測 |
| J: JRuby 反復 | [Actions 36362615236](https://github.com/koduki/example-rails-aot/actions/runs/36362615236)・[生データ](https://github.com/koduki/example-rails-aot/actions/runs/36362615236/artifacts/10947538810)、`f3c67327e71c074ece3622d8f18ef24f41c5263d` | JRuby 4構成×3反復、`GET /articles`、4接続・30秒 closed-loop。ウォームアップ60～600秒 | JRuby の2×2と反復差 |

J の測定用 Rails も 8.0.5.1 だが、派生元ソース、依存関係、コミット、ランナー、ウォームアップと測定時間は S と異なる。**S と J の RPS を直接割って処理系間の優劣を示さない。** 両実験は3記事・3コメントの SQLite WAL fixture を使う。最大メモリは docker stats のコンテナ使用量であり、プロセス RSS ではない。hosted runner の CPU 指定は物理コアの完全な隔離を保証せず、SMT sibling やホスト負荷の影響が残る。

測定前の preflight は9構成の `/articles`、`/articles/1`、`/articles/new`、`/articles.json`、`/articles/1.json` を Rails 基準と照合し、S の全構成で5経路が適格だった。[AOT の全ジョブ](https://github.com/koduki/example-rails-aot/actions/runs/36380159179)も成功した。正常系 read/update と create/delete はそれぞれ9/9 `verified` で、HTTP と保存後の DB を確認した。ただし `verified` は低負荷の機能確認であり、CRUD の有効な性能反復は **0/1**。操作全体の成功操作/秒や p99 の性能倍率は示せない。CRUD は update がフォーム取得＋更新、create/delete がフォーム取得＋作成＋削除であり、操作単位と HTTP 要求単位を分けて集計する。

無効入力の HTML エラー表示と JSON エラー形状には差が残る。不正 CSRF トークンの拒否は、参照用 Rails 自体が検証を無効にしているため `excluded` である。比較器は CSRF 用 meta/input 要素を比較対象から外すが、フォームの内容、HTTP、DB 効果は比較する。**読み取り5経路の適格性はアプリ全体の互換性を意味しない。**

## 5. 結果 S: CRuby と Spinel

各構成1回の観測値であり、最大持続容量ではない。p50/p95/p99 はその試行の HTTP 応答時間である。

| 実行構成 | RPS | p50 / p95 / p99 (ms) | 最大コンテナ使用量 (MB) |
| --- | ---: | ---: | ---: |
| Rails / CRuby YJIT Off | 321.52 | 12.26 / 16.03 / 18.70 | 102.20 |
| Rails / CRuby YJIT On | 546.58 | 7.11 / 11.37 / 13.90 | 126.40 |
| 生成 Ruby / CRuby YJIT Off | 2,407.74 | 1.61 / 2.46 / 3.03 | 41.89 |
| 生成 Ruby / CRuby YJIT On | 3,066.75 | 1.24 / 1.96 / 2.62 | 50.16 |
| Spinel AOT | 4,516.54 | 0.88 / 1.16 / 1.25 | 12.63 |

~~~mermaid
xychart-beta
    title "実験 S: GET /articles の観測 RPS（各1回）"
    x-axis ["Rails Off", "Rails On", "生成 Off", "生成 On", "Spinel"]
    y-axis "RPS" 0 --> 4700
    bar [322, 547, 2408, 3067, 4517]
~~~

| CRuby 内の比較 | 算式 | 観測比 |
| --- | --- | ---: |
| 生成 Ruby / Rails、Off | 2407.74 / 321.52 | **7.489** |
| 生成 Ruby / Rails、On | 3066.75 / 546.58 | **5.611** |
| YJIT On / Off、Rails | 546.58 / 321.52 | **1.700** |
| YJIT On / Off、生成 Ruby | 3066.75 / 2407.74 | **1.274** |
| 相互作用比 | 1.274 / 1.700 | **0.749** |

YJIT On の絶対差は Rails **+225.06 RPS**、生成 Ruby **+659.01 RPS**。On による絶対 RPS の増加と、変換の相対倍率の縮小は両立する。これは同じ短時間条件での観測であり、YJIT が特定の内部処理に作用した割合や定常容量の増分ではない。

Spinel／Rails CRuby Off は **14.047 倍**、Spinel／生成 Ruby YJIT On は **1.473 倍**。HTTP サーバーと SQLite アダプターを含む比較のため、AOT コンパイル単独の倍率として扱わない。

S の Rails/JRuby JIT On は **29.15 RPS**、生成 JRuby JIT On は **1122.00 RPS**。短い1回の比 **38.491** は J の長時間反復中央値の比 **7.488** と大きく違う。JIT 段階と実験条件の差を分離できないので、S の JRuby 値は処理系間の順位付けに使わない。

## 6. 結果 J: JRuby の2×2と反復差

J の12試行はすべて `passed`。局所安定ゲートは15秒窓4つ、CVとドリフト各10%以下、測定30秒と失敗率条件を用いた。[profile](../bench/profiles/ci-jruby-convergence.yml) と成果物の `summary.md`、`trials/per-run.json` が根拠である。

| 構成 | 反復1 / 2 / 3 RPS | 中央値 RPS | 中央値 p50 / p95 / p99 (ms) | ウォームアップ |
| --- | ---: | ---: | ---: | ---: |
| Rails / JRuby OFF | 97.60 / 92.64 / 95.08 | **95.08** | 34.20 / 66.45 / 75.28 | 120～150秒 |
| Rails / JRuby JIT | **105.00** / 301.41 / 313.84 | **301.41** | 12.39 / 20.96 / 26.88 | **211～376秒** |
| 生成 Ruby / JRuby OFF | 1030.37 / 1033.87 / 1034.10 | **1033.87** | 3.40 / 8.15 / 10.40 | 90～109秒 |
| 生成 Ruby / JRuby JIT | 2211.64 / 2312.39 / 2257.07 | **2257.07** | 1.68 / 3.14 / 4.44 | 120～180秒 |

~~~mermaid
xychart-beta
    title "Rails / JRuby JIT: 局所ゲートを通った3反復"
    x-axis ["反復1", "反復2", "反復3"]
    y-axis "RPS" 0 --> 350
    line [105, 301, 314]
~~~

| 中央値の比較 | 算式 | 観測比 |
| --- | --- | ---: |
| 生成 Ruby / Rails、OFF | 1033.87 / 95.08 | **10.874** |
| 生成 Ruby / Rails、JIT | 2257.07 / 301.41 | **7.488** |
| JIT / OFF、Rails | 301.41 / 95.08 | **3.170** |
| JIT / OFF、生成 Ruby | 2257.07 / 1033.87 | **2.183** |
| 相互作用比 | 2.183 / 3.170 | **0.689** |

同じ反復番号で組むと、生成 Ruby／Rails の JIT 比は **21.06、7.67、7.19**、OFF 比は **10.56、11.16、10.88**。それぞれの相互作用比は約 **2.00、0.69、0.66** となり、反復1だけ方向が逆転する。Rails/JRuby JIT の反復1は局所窓では安定しても、残る2回と同じ処理量段階ではなかった。ホスト、JVM/JRuby のコンパイル、GC、試行順の寄与は未分離である。したがって中央値から得た **0.689** に一般的な相互作用の精度を与えない。

JRuby OFF は `compile.mode=OFF` であり JVM JIT を切った条件ではない。表の p99 中央値は各試行の p99 の中央値で、全応答をプールした p99 ではない。

## 7. 考察と次の検証

CRuby の YJIT と JRuby の `compile.mode` は異なる操作であり、S と J はソース・ランナー・測定時間も異なる。両実験で「On の絶対 RPS 増加、生成 Ruby／Rails の相対倍率縮小」という**方向**は共通するが、CRuby と JRuby の効果量や優劣を横断比較しない。

生成 Ruby は Rails と比べて JIT が改善できる仕事の構成、待ち時間、DB/HTTP の比重が違う可能性がある。相対倍率の縮小を「JIT と Roundhouse の相性が悪い」と解釈する証拠はない。CPU 時間、割当、GC、JIT 統計、SQL、HTTP 待機を分解してはじめて原因を検証できる。

| 優先 | 実験 | 判定基準 |
| --- | --- | --- |
| 1 | 同一ソース・ホスト・fixture・endpoint で CRuby/JRuby の8構成を交差順に反復 | 各反復の分布とドリフト、長い測定後の時系列を提示。JRuby/JIT の段差を追跡 |
| 2 | k6 open-arrival の段階的負荷率探索 | 同一 SLO に対する p99、失敗率、drop、負荷生成器の余力で持続容量を定義 |
| 3 | 正常系 CRUD の長時間反復 | フォーム取得を含む論理操作全体の成功操作/秒と p50/p95/p99。無効入力と CSRF 保護は機能上の別評価 |
| 4 | 原因の profile と Spinel 対照 | CPU、JIT、GC/割当、SQL、HTTP を分離。AOT 単独の寄与を論じるなら HTTP/DB 層も揃える |

専用ホストでの容量探索は未実施。[測定契約](benchmark.md)の full profile は7構成×5経路×5反復の175試行で、最短ウォームアップと測定だけでも14時間35分を要する。アプリと負荷生成器の論理 CPU に加え、SMT sibling も分離する必要がある。

**結論:** この小規模な読み取り workload では Roundhouse の生成 Ruby と Spinel の実行スタックに高い観測 RPS が得られた。On は絶対 RPS を改善し、生成 Ruby／Rails の相対倍率は縮んだ。JRuby の段差と実験条件の差があるため、処理系ごとの差の大きさや内部原因は未確定である。CSRF 検証を無効にした実験結果を保護された本番 Rails アプリへの導入効果に外挿しない。
