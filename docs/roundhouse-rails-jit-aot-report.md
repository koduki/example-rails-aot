# Roundhouse × Rails × JIT / Spinel AOT 検証レポート

**更新日:** 2026-09-28

**照合した main:** [`a2627534e0b00cf08750621ebbb81b09b8fc46da`](https://github.com/koduki/example-rails-aot/commit/a2627534e0b00cf08750621ebbb81b09b8fc46da)
**対象:** このリポジトリの小規模な blog アプリとベンチマーク用コピー。一般的な Rails アプリの性能を代表する測定ではない。

## 要旨と証拠の強さ

Roundhouse で生成した Ruby は、このアプリの `GET /articles` について、元の Rails より高い**観測処理量**を示した。CRuby の初回 smoke では YJIT Off で 8.38 倍、On で 6.75 倍。後続の JRuby 収束パイロットの各構成の RPS 中央値から計算すると、JRuby `compile.mode=OFF` で 10.87 倍、`JIT` で 7.49 倍だった。ただし**両実験は条件と時期が異なる**。CRuby と JRuby の倍率の大小を、そのまま処理系固有の変換効果の差とは解釈できない。

| 証拠 | 観測できたこと | まだ言えないこと |
| --- | --- | --- |
| [初回 smoke](benchmark-results.md)・[Actions #36289166814](https://github.com/koduki/example-rails-aot/actions/runs/36289166814) | 7構成各1回、4接続・10秒の closed-loop 観測。読み取り5経路は事前比較で適格 | 最大容量、定常倍率、JITの内部寄与。`emit-jruby` は `unstable` |
| [JRuby 収束パイロット](benchmark.md#benchmark-only-crud-scope)・[Actions #36362615236](https://github.com/koduki/example-rails-aot/actions/runs/36362615236) | JRuby 4構成×3回の `/articles` が `passed`。60～600秒のウォームアップと30秒の closed-loop 測定 | 反復をまたぐ同一の定常段階、容量、CRubyとの統制比較。Rails/JRuby JIT On の反復差は大きい |
| [正常系 CRUD の CI](https://github.com/koduki/example-rails-aot/actions/runs/36362615236) | 全9構成で read/update と create/delete がそれぞれ9/9 `verified`。DB状態も照合 | `verified` は低負荷の機能確認。書き込み性能値や全機能の互換性ではない |
| [AOT 機能検証](../.github/workflows/aot.yml) | Spinel のビルド、HTTP・DB 操作、Ruby を含まない実行コンテナ | Spinel コンパイラ単独の加速率、運用上の起動時間・費用対効果 |

**現段階の結論:** 変換後の実行スタックには大きな改善余地が見える。YJIT と JRuby の切替にも観測差がある。一方で「Roundhouse が JIT の効率を高めた」「ボトルネックが SQLite に移った」「Spinel 化だけで16倍速い」は、今回のデータからは実証できない。以下では観測値、計算、機序の仮説を分ける。

## 1. 比較している実行スタック

元の [`blog/`](../blog/) は Rails 8.1.3.1。性能測定では JRuby 側の依存性に合わせ、[`prepare_app.py`](../scripts/bench/prepare_app.py) が Rails 8.0.5.1 の使い捨てコピーを作る。CRuby 3.4.5、JRuby 10.0.7.0、固定版 Roundhouse v2026.9.18、Spinel 2026.09.12 を使用する。バージョンと差分の来歴は [provenance](provenance.md)、設定は [`bench/targets.yml`](../bench/targets.yml) と [`bench/toolchain.env`](../bench/toolchain.env) にある。

| 系列 | 元アプリ | Roundhouse 出力 | 切替の意味 |
| --- | --- | --- | --- |
| CRuby | `rails-cruby-off` / `rails-cruby-yjit` | `emit-cruby-off` / `emit-cruby-yjit` | 同じ CRuby 上で YJIT 無効／有効 |
| JRuby | `rails-jruby-off` / `rails-jruby` | `emit-jruby-off` / `emit-jruby` | **JRuby 自身の** `compile.mode=OFF`／`JIT`。両方とも JVM の HotSpot JIT は有効 |
| Spinel | 直接 Rails を Spinel 化する構成はない | `spinel` | Roundhouse 出力を Spinel で C 経由のネイティブ実行ファイルにする。JIT の切替対象外 |

Roundhouse は Rails ソースを取り込み、型・副作用などを解析し、Rails 固有の記法を明示的な処理へ lower してターゲット別プロジェクトを出す。生成物は元の Rails gem をそのまま実行するのではなく、共通の生成フレームワークとターゲット別ランタイムを使う。したがって Rails 対 emitted の比較は、単一の最適化パスの効果だけでなく、**プログラム形状とランタイムの置換を含む**。[固定版の構成説明](provenance.md#appendix-roundhouse-architecture-v2026918)を参照。

Spinel と CRuby/JRuby では HTTP サーバー、DB アダプター、SQLite の実装版、VM の有無も変わる。Spinel と Rails の差を「AOT コンパイラ単体の効果」とするのは誤りである。生成時の `lower_residue` 警告は JSON エラー応答や未使用の mailer に残っている。

## 2. 機能ゲートと測定の条件

同じ固定 fixture は3記事・3コメントを持つ SQLite WAL。初回の preflight は9構成で `/articles`、`/articles/1`、`/articles/new`、`/articles.json`、`/articles/1.json` の HTTP 応答と DB 状態を Rails 基準で比較した。HTML の動的トークン等は規定に従って正規化する。合格は**この5経路の適格性**であり、アプリ全体の完全一致ではない。

初回 smoke は hosted runner 上の1構成1試行、4接続・10秒の closed-loop。アプリ論理 CPU 0、負荷側 1～3 で、CPU 0 と 1 は同一物理コアの SMT sibling だった。クライアント側と完全に分離できていない。予備測定のメモリは `docker stats` のコンテナ使用量で、プロセス RSS ではない。CPU 100% はおおむね1論理 CPU 相当。結果の provenance と元の計測コミット `e1dd3903de9dfcc8b87a16f222d0ebe43ca8c614` は[初回記録](benchmark-results.md)を参照。

その後、**ベンチマーク用 Rails コピーだけ** CSRF 検証を無効化して、現行の変換後出力と正常系 CRUD の条件を揃えた。元の `blog/` の保護は維持される。全9構成の読み取り・正常更新と正常作成・削除は `verified` となったが、不正 CSRF トークンの拒否は比較用 Rails 自体で行わないため、その検査は `excluded`。無効入力の HTML/JSON エラーにも差が残る。書き込みの安全性・全体互換性は未達である。[実行と判定の契約](benchmark.md#benchmark-only-crud-scope)を参照。

`verified` は5秒窓に数件しかない低負荷の CI 機能確認で、統計性能表から除外される。CRUD は1操作に複数 HTTP 要求を含むため、後続の性能試験では**論理操作全体の時間と操作数/秒**を単位にする必要がある。この操作単位の計測は [PR #36](https://github.com/koduki/example-rails-aot/pull/36) で提案中であり、照合時点の main にはまだ入っていない。現行の `verified` を操作性能の根拠にしてはいけない。

## 3. 初回 CRuby・Spinel smoke の観測値

以下は[初回測定の生データからの集計](benchmark-results.md)。`passed` であっても定常容量の認定ではない。JRuby の初回値も経緯として併記し、`unstable` の組を倍率に使わない。

| 構成 | 判定 | RPS | p50 / p95 / p99 (ms) | 最大コンテナメモリ (MB) |
| --- | --- | ---: | ---: | ---: |
| Rails / CRuby YJIT Off | passed | 270.60 | 14.55 / 19.16 / 21.30 | 109.30 |
| Rails / CRuby YJIT On | passed | 447.53 | 8.66 / 13.32 / 15.90 | 133.60 |
| emitted / CRuby YJIT Off | passed | 2,268.48 | 1.72 / 2.54 / 2.98 | 41.58 |
| emitted / CRuby YJIT On | passed | 3,021.03 | 1.29 / 1.99 / 2.42 | 50.16 |
| Rails / JRuby compile.mode=JIT | passed | 26.86 | 121.38 / 240.92 / 254.24 | 616.70 |
| emitted / JRuby compile.mode=JIT | **unstable** | 1,258.74 | 2.48 / 6.22 / 9.40 | 424.90 |
| emitted / Spinel AOT | passed | 4,353.36 | 0.89 / 1.17 / 1.25 | 12.54 |

この**一試行に限れば**、Roundhouse / Rails は CRuby YJIT Off で `2268.48 / 270.60 = 8.383`、On で `3021.03 / 447.53 = 6.750`。Rails 内の YJIT On / Off は `1.654`、emitted 内では `1.332`。倍率の比（相互作用比）は `1.332 / 1.654 = 0.805`。YJIT に伴う観測 RPS 差は Rails で `+176.93`、emitted で `+752.55` だが、これを「追加で提供できる持続容量」と呼ぶことはできない。closed-loop で負荷到着率を固定せず、負荷・ホストのばらつきも測っていないためである。

Spinel / Rails CRuby YJIT Off の観測 RPS 比は `4353.36 / 270.60 = 16.09`、Spinel / emitted CRuby YJIT On なら `4353.36 / 3021.03 = 1.44`。後者も全スタック差であり、AOT 部分の寄与は分離されていない。12.54 MB は当該試行のコンテナ値で、起動直後の RSS やサーバーレス請求メモリではない。

## 4. JRuby 収束パイロット: CRuby との傾向、そして反復差

[2026-09-28 の Actions #36362615236](https://github.com/koduki/example-rails-aot/actions/runs/36362615236) の [artifact](https://github.com/koduki/example-rails-aot/actions/runs/36362615236/artifacts/10947538810) 内 `bench-results/jruby-convergence/summary.md` と `trials/per-run.json` を照合した。`ci-jruby-convergence.yml` は JRuby の4構成、`/articles`、3反復、4接続・30秒の closed-loop。ウォームアップは最短60秒・最大600秒、15秒窓4つ、CV/ドリフト各10%以下を目安とし、ウォームアップ・測定の失敗率ゲートも適用する。12試行すべて `passed`。これは**各試行の局所的な収束判定**である。

| JRuby 構成 | 各反復の RPS（実行順ではなく反復番号順） | 中央値 RPS | 中央値 p50 / p95 / p99 (ms) | 試行別ウォームアップ時間の範囲 |
| --- | ---: | ---: | ---: | ---: |
| Rails / JRuby JIT Off | 97.60, 92.64, 95.08 | **95.08** | 34.20 / 66.45 / 75.28 | 120～150秒 |
| Rails / JRuby JIT On | **105.00**, 301.41, 313.84 | **301.41** | 12.39 / 20.96 / 26.88 | **211～376秒** |
| emitted / JRuby JIT Off | 1030.37, 1033.87, 1034.10 | **1033.87** | 3.40 / 8.15 / 10.40 | 90～109秒 |
| emitted / JRuby JIT On | 2211.64, 2312.39, 2257.07 | **2257.07** | 1.68 / 3.14 / 4.44 | 120～180秒 |

ここでの「JRuby JIT Off」は `compile.mode=OFF` を意味し、**HotSpot JIT は有効**。したがって ON / OFF 比は JVM JIT 全体の効果ではなく JRuby のコンパイルモードを切り替えた実行スタックの観測差である。診断採取のオーバーヘッドがある場合には通常の順位とも混ぜない。[ランタイム状態の説明](../bench/README.md#targets-and-compatibility)を参照。

各構成の中央値から計算した2×2表は次の通り。中央値 p99 は各試行の p99 の中央値であり、全リクエストをプールした p99 ではない。

| 比較 | 計算 | RPS 中央値比 |
| --- | --- | ---: |
| Roundhouse 効果、JRuby JIT Off | `1033.87 / 95.08` | **10.874** |
| Roundhouse 効果、JRuby JIT On | `2257.07 / 301.41` | **7.488** |
| JRuby モード切替、Rails | `301.41 / 95.08` | **3.170** |
| JRuby モード切替、emitted | `2257.07 / 1033.87` | **2.183** |

この試行の JRuby 相互作用比は `2.183 / 3.170 = 0.689`。観測上、JIT On でも変換後は速いが、**変換の倍率は Off の方が大きい**。CRuby の予備試行でも YJIT On 時に Roundhouse / Rails 倍率が Off 時より小さい（6.75 対 8.38、相互作用比0.805）。「JIT と変換の倍率は常に相乗的に増える」という予想とは異なる方向である。

ただし JRuby は反復1の Rails/JIT On が **105.00 RPS**、反復2・3が **301.41 / 313.84 RPS**。同じ反復番号で組むと emitted / Rails JIT On 比は **21.06、7.67、7.19**、JIT Off 比は **10.56、11.16、10.88**。相互作用比は順に**約2.00、0.69、0.66**となり、最初の反復だけ方向が逆転する。各試行で窓の局所CVが小さくても、同じ長期的な JIT 段階に到達したとは限らない。順序、起動状態、JVM/JRuby のコンパイル段階、hosted runner の揺らぎを分離できず、0.689 を一般化された処理系特性と断定できない。3反復では信頼区間も安定して推定できない。

**CRuby と JRuby の差について:** JRuby の JIT モード比（中央値3.17 / 2.18）は CRuby の初回 YJIT 比（1.65 / 1.33）より数値上大きく、JRuby のウォームアップ時間と反復差も目立つ。しかし JRuby は後続の長い別 run、CRuby は10秒の初回 smoke であり、JRuby の OFF でも JVM JIT が動く。これらの数値を使って「JRuby の JIT が YJIT より強い」「Roundhouse は JRuby でより効果的」とは結論できない。同一ホスト・同一 profile で CRuby と JRuby の全8構成を交互に反復し、ウォームアップと長期ドリフトを揃えて初めて程度の差を比較できる。

## 5. 機序の再評価: 分かることと追加で必要な測定

Roundhouse が Rails の動的な処理を事前に明示化し、リクエスト時の仕事を変えるのは[生成設計](provenance.md#appendix-roundhouse-architecture-v2026918)と整合する。観測 RPS とメモリの差もこの仮説に合う。しかし本測定は、ルート探索・SQL構築・テンプレート・割当・GC・HTTP・DB にかかった時間を分解していない。旧稿の「Ruby CPU 75%→38%」「SQLite/OS が62%」「ガード失敗低減」「GCポーズ激減」は計測根拠がなく撤回する。アムダールの法則は**改善不能な割合を独立に測った後**のモデルであり、p50 を仮に Ruby 時間と I/O 時間へ割り振って証明することはできない。

RPS の絶対差と倍率はどちらも観測の記述には有用だが、処理能力を判断するには到着率を段階的に上げ、同じ SLO（p99・失敗率・drop）を満たす最大負荷を各構成で探す必要がある。固定 offered RPS の k6 は**同じ負荷における遅延・エラー・資源量**を比べるもの。最大容量の比は別の負荷率探索から算出する。closed-loop では遅延増大に伴って到着率も下がるため、飽和時のテイル遅延を過小評価しやすい。

機序を検証するなら、同じ endpoint・fixture・CPU の下で、まず割当/GC・YJIT stats・JRuby と JVM のコンパイルログ・CPU profile・DB 待機/クエリ・サーバー待機を採る。計測用フラグはオーバーヘッドを生むので、診断 run と速度の基準 run を分離する。JRuby では局所収束に加え、測定後にも複数の長い窓を設け、同じ構成の反復間でスループット段階が揃うか確認する。生成 Ruby の単純な呼び出し構造が JIT に有利か、単に Rails の仕事が減った結果なのかはこの時点でも対照実験が必要である。

Spinel の独立寄与を問うなら、同じ生成プログラム・同等の HTTP/DB 層に近づけた対照を作る。現状の 16.09 倍は **Rails/CRuby と Roundhouse/Spinel の全スタック差**であり、コールドスタートの実測もない。費用削減率や本番の Scale-to-Zero 性能を数値で約束する根拠にはならない。

## 6. 再現・次の判定条件

1. [初回結果](benchmark-results.md) のコミットと artifact を固定して歴史的観測を確認する。現行 main の測定と数値を混ぜない。
2. [`ci-jruby-convergence.yml`](../bench/profiles/ci-jruby-convergence.yml) の12試行について、`summary.md` だけでなく `trials/per-run.json` と `warmup.json` の反復差、試行状態、失敗と transport retry を検査する。`unstable` と `verified` を倍率に入れない。
3. 専用ホストで物理コアの SMT sibling を検査し、アプリと負荷生成器を分離する。CRuby/JRuby 全8構成を同条件で順序を回しながら反復し、ウォームアップと長期ドリフト、各反復の範囲を示す。ホスト間の実験には測定環境を記録する。
4. open-arrival の負荷率探索で事前に固定した SLO を満たす最大値を比較する。固定到着率では p50/p95/p99、エラー、`dropped_iterations`、クライアント飽和、CPU/コンテナメモリを併記する。CRUD は正常系の操作単位で測り、CSRF・無効入力の互換性は別に修正・検証する。

現行の [`full.yml`](../bench/profiles/full.yml) は7構成×5経路×5反復の175試行で、最短のウォームアップと測定だけで14時間35分を要する。GCE 実機での本測定はまだ実施していない。[実行契約](benchmark.md) と [残作業](benchmark-remaining-work.md) を参照。結論を更新する際は「どの commit、どの profile、どの状態の試行か」を先に示し、実測値からの算術と因果的な説明を区別する。
