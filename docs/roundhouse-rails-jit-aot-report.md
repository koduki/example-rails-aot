# Roundhouse × Rails × JIT / Spinel AOT 検証レポート

**更新日:** 2026-09-28

**対象:** このリポジトリの小規模な記事・コメントアプリ。数値は[初回 smoke](benchmark-results.md)と[JRuby 収束パイロット](https://github.com/koduki/example-rails-aot/actions/runs/36362615236)の歴史的 artifact に属する。

## 1. サマリー

Rails アプリを Roundhouse で変換して生成 Ruby または Spinel ネイティブ実行ファイルとして動かすと、このアプリの GET /articles では元の Rails より高い**観測処理量**を示した。CRuby の10秒 smoke で変換後／Rails は YJIT Off **8.38倍**、On **6.75倍**。別の JRuby 収束パイロットで各構成の3反復中央値から計算すると、JRuby compile.mode=OFF **10.87倍**、JIT **7.49倍**だった。

JIT を On にすると**絶対 RPS は両方のアプリ形状で上がる**。下がったのは「変換後／Rails」という相対倍率である。JRuby では Rails/JIT On の反復が **105、301、314 RPS** と大きくばらつくため、変換と JIT の相互作用の大きさ、その内部機構を確定できない。

| 論点 | このリポジトリから言えること | まだ言えないこと |
| --- | --- | --- |
| Roundhouse | 限定した読み取りで生成 Ruby の観測 RPS が高い | 一般の Rails アプリで同じ倍率、最大持続容量 |
| YJIT / JRuby のモード | 各実験の On が Off より高い RPS。変換の相対倍率は On で小さい | JIT 内部のコンパイル効率、CRuby と JRuby の優劣 |
| Spinel | 初回 smoke で 4,353 RPS、最大コンテナ使用量 12.54 MB | AOT コンパイラ単独の効果、起動時間やクラウド費用 |
| 機能 | 読み取り5経路が preflight 適格。正常系 CRUD の短い CI は9構成すべて verified | 無効入力の完全一致、CSRFを有効にしたアプリとの互換性、CRUD容量 |

**解釈上の境界:** hosted runner の小さな fixture によるパイロット観測である。今回、正本アプリを Rails 8.0.5.1・CSRF無効の検証 fixture に揃えた。**新しいコミットの preflight と性能測定を別の結果として扱う**。過去の artifact を新しいソースの実測値として読み替えない。

## 2. アーキテクチャと比較の単位

正本は [blog/](../blog/) の Rails **8.0.5.1**。このリポジトリは Rails から Roundhouse を経由して Spinel AOT へ至る可否と、生成 Ruby の JIT 特性を検証する実験である。CSRF 検証は**実験アプリ全体で無効**にする。生成ランタイムの現状と正常系書き込みの条件を揃えるための選択であり、保護された Rails アプリや本番公開の安全性を示さない。

~~~mermaid
flowchart TD
    A["blog/: Rails 8.0.5.1、CSRF無効"] --> B["AOT経路: Roundhouse"]
    A --> C["測定経路: 派生コピー"]
    B --> D["生成 Ruby: CRuby / JRuby"]
    B --> E["Spinel → C → ネイティブ"]
    C --> F["元 Rails: CRuby / JRuby"]
    D --> G["HTTP / DB 正確性ゲートと測定"]
    E --> G
    F --> G
~~~

Roundhouse の固定版 v2026.9.18 は Rails ソース・ERB・スキーマ・ルートを取り込み、型や副作用を解析し、Rails 固有の表現を明示的なコードに lower してターゲット別のプロジェクトを出力する。生成物は元の Rails gem をそのまま実行する構成ではない。共通の生成フレームワークとターゲット別の手書きランタイムが組み合わさる。[固定版の構造と警告](provenance.md#appendix-roundhouse-architecture-v2026918)を参照。

| 比較 | 変えるもの | 主に確認できるもの | 同時に変わるもの |
| --- | --- | --- | --- |
| Rails 対 emitted、CRuby YJIT 固定 | ソース形状と生成ランタイム | Roundhouse 経路全体の差 | フレームワーク処理、呼び出し、生成 HTTP/DB 層 |
| YJIT Off 対 On、形状固定 | CRuby の YJIT 設定 | この条件での JIT 切替の観測差 | ウォームアップ、コードキャッシュ、メモリ |
| JRuby compile.mode=OFF 対 JIT、形状固定 | JRuby の IR/バイトコード生成モード | この切替の観測差 | JVM の JIT は**両方で有効** |
| Rails/CRuby 対 Roundhouse/Spinel | VM、生成コード、HTTP、DB アダプター | 実行スタック全体の差 | 複数要素。Spinel コンパイラ単独には分解できない |

比較用コピーは [prepare_app.py](../scripts/bench/prepare_app.py) が blog/ から生成する。正本と同じ Rails 8.0.5.1 を使い、JRuby JDBC を含む別 lockfile、起動・DB・Puma 設定を適用する。Rails 8.1→8.0 へのソース書き換えは不要となった。正本と性能測定時の設定差は manifest に記録する。

## 3. 仮説と検証可能な予測

設計からもっともらしい説明と、観測で確かめたことを区別する。下の「必要な証拠」はまだ揃っていない。

| 仮説 | 予測 | 現在の支持材料 | 決着に必要な証拠 |
| --- | --- | --- | --- |
| H1: Roundhouse がリクエスト時の Rails 抽象化を減らす | 同じ処理系・JIT設定で生成 Ruby の処理時間と割当が減る | CRuby と JRuby の限定 endpoint で生成側の観測 RPS が高い | CPU flamegraph、割当/GC、SQL・ルーティング・描画時間の分解 |
| H2: 生成コードは JIT の最適化に向く | 同じ仕事量とウォームアップで JIT の寄与率または時間短縮が増す | **未確認**。両処理系で On の絶対 RPS は増えるが相対的な変換倍率は縮む | YJIT/JRuby/JVM のコンパイル統計、同一負荷の profile、反復した対照実験 |
| H3: JRuby では長いウォームアップが結果を変える | 短い smoke と長い run の順位・倍率が変わり、同じ設定でも段階差が残りうる | 初回 emitted は unstable。後続12試行は通過したが Rails/JIT On に105→301→314 RPS の段差 | 長い事後観測、コンパイルログと GC、run 順序の交差 |
| H4: Spinel の軽い実行スタックが有利 | ネイティブ構成のコンテナ資源・遅延が小さい | 初回 smoke の観測に整合 | 同等の HTTP/DB 層での対照、起動・アイドル・定常の反復測定 |

旧稿にあった「Ruby CPU が75%から38%へ減った」「SQLite/OSが62%を占める」「型ガード失敗が減った」「GCポーズが劇的に減った」は計測していない。アムダールの法則は改善不能部分を独立に特定して初めて量的説明に使える。今回は上の**仮説**として扱う。

## 4. 実験契約と判定

### 歴史的データを区別する

| 実験 | コミット・成果物 | 対象と負荷 | 用途 |
| --- | --- | --- | --- |
| 初回 smoke | e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614・[Actions #36289166814](https://github.com/koduki/example-rails-aot/actions/runs/36289166814) | 主7構成、各1回、GET /articles、4接続・10秒 closed-loop。最大45秒ウォームアップ | 観測値と測定器の予備検証 |
| JRuby 収束パイロット | f3c67327e71c074ece3622d8f18ef24f41c5263d・[Actions #36362615236](https://github.com/koduki/example-rails-aot/actions/runs/36362615236)・[artifact](https://github.com/koduki/example-rails-aot/actions/runs/36362615236/artifacts/10947538810) | JRuby 4構成×3回、GET /articles、4接続・30秒 closed-loop。ウォームアップ60～600秒 | JRuby モードの2×2と反復差 |
| 正常系 CRUD CI | [同 Actions の artifact](https://github.com/koduki/example-rails-aot/actions/runs/36362615236/artifacts/10947538810) | 全9構成、低い固定投入率、read/update と create/delete | HTTP・DB 効果の機能確認のみ |

初回 smoke は Rails 8.0.5.1 の**当時の派生コピー**を使った。元の blog/ は当時 Rails 8.1.3.1 だった。歴史的な provenance は[初回結果](benchmark-results.md)に保存する。今回の正本変更後はソース hash が変わるため、旧 preflight manifest は再利用できない。

初回 hosted runner はアプリ論理 CPU 0、負荷生成側 1～3で、0と1が SMT sibling だった。物理コアを完全に隔離した測定ではない。固定 fixture は3記事・3コメント、SQLite WAL。最大メモリは docker stats の**コンテナ使用量**であり、Ruby プロセス RSS ではない。

### 正確性ゲート

preflight は9構成の /articles、/articles/1、/articles/new、/articles.json、/articles/1.json を Rails 基準と比較した。動的トークンなど指定された値を正規化し、HTTP と DB の結果を照合する。合格は**この5読み取り経路**の適格性であり、全アプリの互換性ではない。

変更前の正常な更新・作成・削除の CI は各シナリオで9/9 verified。測定後の DB を確認した。一方、無効入力の HTML エラー表現と JSON エラー形状には差が残る。**不正 CSRF トークンの拒否を試すケースは excluded** である。正本 Rails 自体が検証を無効化しているため、Rails と生成物がともに書き込んでも「拒否の同等性」とはならない。比較器は CSRF 用の meta/input 要素だけを無視し、記事フォームの値・HTTP・DB は引き続き比較する。レポートを外部公開する際にはこの制限を明示する。

verified は5秒窓に数操作程度の機能確認であり、性能の passed とは異なる。CRUD の論理操作は update がフォーム取得＋更新、create/delete がフォーム取得＋作成＋削除の複数 HTTP 要求。レポーターは操作全体の遅延と**成功操作/秒**を別単位として扱い、HTTP 要求単位の raw 指標と混ぜない。現時点で比較に採用できる CRUD の収束・反復性能測定はない。

## 5. 結果: CRuby と Spinel の初回 smoke

下表とグラフは**同じ短い run の観測値**であり、容量順位ではない。初回の JRuby emitted はウォームアップ未収束のため、後の JRuby 図に含めない。

| 実行構成 | 状態 | RPS | p50 / p95 / p99 (ms) | 最大コンテナメモリ (MB) |
| --- | --- | ---: | ---: | ---: |
| Rails / CRuby YJIT Off | passed | 270.60 | 14.55 / 19.16 / 21.30 | 109.30 |
| Rails / CRuby YJIT On | passed | 447.53 | 8.66 / 13.32 / 15.90 | 133.60 |
| emitted / CRuby YJIT Off | passed | 2,268.48 | 1.72 / 2.54 / 2.98 | 41.58 |
| emitted / CRuby YJIT On | passed | 3,021.03 | 1.29 / 1.99 / 2.42 | 50.16 |
| emitted / Spinel AOT | passed | 4,353.36 | 0.89 / 1.17 / 1.25 | 12.54 |

~~~mermaid
xychart-beta
    title "初回 smoke: GET /articles の観測 RPS（各1回）"
    x-axis ["Rails Off", "Rails YJIT", "生成 Off", "生成 YJIT", "Spinel"]
    y-axis "RPS" 0 --> 4500
    bar [271, 448, 2268, 3021, 4353]
~~~

CRuby 内の2×2から、変換後／Rails は Off 2268.48 / 270.60 = **8.383**、On 3021.03 / 447.53 = **6.750**。YJIT On／Off は Rails **1.654**、生成側 **1.332**、相互作用比は **0.805**。YJIT On の観測絶対差は Rails **+176.93 RPS**、生成側 **+752.55 RPS**。**絶対差の増加と改善倍率の低下は同時に起こりうる**。この RPS 差を、持続的に追加で提供できる容量とは呼ばない。

Spinel / Rails CRuby Off の初回観測比は **16.09**。生成 Ruby の YJIT On と比べると **1.44**。どちらも HTTP サーバーと SQLite アダプターを含む全スタックの差である。起動時間、並列スケーリング、コストは測定していない。

## 6. 結果: JRuby の2×2とウォームアップ

後続の専用パイロットでは、JRuby 4構成×3反復の12試行がすべて passed。局所安定ゲートは15秒窓4つ、CVとドリフト各10%以下、ウォームアップ60～600秒、測定30秒と失敗率条件を使う。[profile](../bench/profiles/ci-jruby-convergence.yml) と artifact の summary.md・trials/per-run.json が根拠である。

| 構成 | 反復1 / 2 / 3 RPS | 中央値 RPS | 中央値 p50 / p95 / p99 (ms) | ウォームアップ範囲 |
| --- | ---: | ---: | ---: | ---: |
| Rails / JRuby OFF | 97.60 / 92.64 / 95.08 | **95.08** | 34.20 / 66.45 / 75.28 | 120～150秒 |
| Rails / JRuby JIT | **105.00** / 301.41 / 313.84 | **301.41** | 12.39 / 20.96 / 26.88 | **211～376秒** |
| emitted / JRuby OFF | 1030.37 / 1033.87 / 1034.10 | **1033.87** | 3.40 / 8.15 / 10.40 | 90～109秒 |
| emitted / JRuby JIT | 2211.64 / 2312.39 / 2257.07 | **2257.07** | 1.68 / 3.14 / 4.44 | 120～180秒 |

~~~mermaid
xychart-beta
    title "Rails / JRuby JIT On: 通過した3反復にも段差がある"
    x-axis ["反復1", "反復2", "反復3"]
    y-axis "RPS" 0 --> 350
    line [105, 301, 314]
~~~

| 中央値からの比較 | 計算 | 観測比 |
| --- | --- | ---: |
| Roundhouse、JRuby OFF | 1033.87 / 95.08 | **10.874** |
| Roundhouse、JRuby JIT | 2257.07 / 301.41 | **7.488** |
| JRuby モード切替、Rails | 301.41 / 95.08 | **3.170** |
| JRuby モード切替、emitted | 2257.07 / 1033.87 | **2.183** |
| 相互作用比 | 2.183 / 3.170 | **0.689** |

しかし同じ**反復番号**で組むと、Roundhouse／Rails の JIT On 比は **21.06、7.67、7.19**、OFF 比は **10.56、11.16、10.88**。相互作用比は**約2.00、0.69、0.66**となり、初回だけ方向が逆転する。Rails/JRuby JIT On の初回は局所的には収束しても、後の2回と同じ処理量段階ではなかった。ホストの揺らぎ、JVM/JRuby のコンパイル段階、実行順序などの寄与は未分離である。少数反復の中央値だけで **0.689** を普遍的な特性とみなせない。

JRuby の OFF は **JRuby 自身の compile.mode=OFF** であり、HotSpot の JVM JIT を切っていない。したがってこの比率は「JVM JIT の有無」ではない。また各試行の p99 中央値は全 HTTP 応答をプールした p99 とは異なる。

## 7. CRuby と JRuby の傾向をどう読むか

両方の実験で On は Rails、生成側それぞれの RPS を上げ、Roundhouse の**相対倍率**は On で小さくなった。だが CRuby の YJIT と JRuby の compile.mode は別の操作で、CRuby は一回10秒、JRuby は別 run の長い3反復。JRuby OFF でも JVM JIT は動く。次の表は**同条件の処理系対決ではない**。

| 実験 | Rails の On / Off | 生成側の On / Off | 変換倍率の On / Off 間相互作用 |
| --- | ---: | ---: | ---: |
| CRuby 初回 smoke | 1.654 | 1.332 | 0.805 |
| JRuby 後続パイロット、中央値比 | 3.170 | 2.183 | 0.689（反復差に注意） |

変換後に元の Rails より少ない仕事で応答するなら、JIT が改善できる部分の割合、サーバーの待ち、DB/HTTP の比重も変わりうる。この説明は**候補**であり、旧稿が示した Ruby 実行時間や SQLite/OS の割合は測っていない。「JRuby の JIT が YJIT より強い」「Roundhouse は JRuby の方が効く」「JIT と Roundhouse の相性が悪い」のいずれも、この2実験を跨いで断定できない。

検証には同一コミット・ホスト・fixture・endpoint・負荷条件で CRuby/JRuby の8構成を交差順序で反復する。ウォームアップだけでなく測定後の長い窓も記録し、Rails/JRuby JIT On の段差を追う。速度の基準 run と、JIT/GC/CPU 診断フラグを有効にした run は分ける。

## 8. 実務的な評価と次の実験

| 優先 | 実験 | 合格・解釈の条件 |
| --- | --- | --- |
| 1 | 新しい正本 Rails 8.0.5.1 で全ターゲットを再 build・preflight | ソース hash、lockfile、イメージ ID、HTTP/DB 比較、CSRF 除外を保存。歴史的値と別系列にする |
| 2 | CRuby/JRuby 8構成の同条件反復 | 反復ごとの分布とドリフトを示す。局所窓だけで JRuby の定常段階を確定しない |
| 3 | k6 open-arrival の段階的負荷率探索 | 同一 SLO の p99・失敗率・drop・クライアント余力で容量を定義。固定 offered rate はその負荷における遅延・資源比較に限定 |
| 4 | 正常系 CRUD の長時間反復 | 操作全体の成功操作/秒・p50/p95/p99を用いる。無効入力と CSRF 保護の欠落は性能と別に評価 |
| 5 | 原因のプロファイリングと Spinel 対照 | GC/割当、JIT stats、CPU、SQL、HTTP待機を分離。AOT 単独の寄与を語るならサーバー/DB 層も揃える |

専用 GCE 実機での容量測定は未実施。[実行契約](benchmark.md)の full.yml は7構成×5経路×5反復、計175試行で、最短ウォームアップと測定だけで14時間35分を要する。アプリと負荷生成器の論理 CPU だけでなく、SMT sibling も分離する必要がある。

**結論:** 小さな読み取り workload では Roundhouse 出力が有望で、Spinel は全スタックとしてさらに軽い観測値を出した。JIT On は絶対 RPS を改善する一方、Roundhouse の相対倍率は今回の両パイロットで縮んだ。JRuby の段差と異なる実験条件があるため、処理系ごとの差や原因を確定する段階には達していない。CSRFを無効化した実験アプリの結果を、保護された本番 Rails アプリへの導入効果と混同しない。
