# S71 可観測性 Implementation Plan

**Goal:** ワークスペースの知識が「使われているか・守られているか」を 1 つの集計（`WorkspaceHealth`）にまとめ、REST と画面で出す。

**Architecture:** `domain/observability.py`（表示の型だけ）→ `services/observability.py` の `workspace_health(session, workspace, *, now)` が DB から 1 回で集計（プロジェクト数に比例したクエリを出さない）→ `api/routes/observability.py` の `GET /api/workspaces/{slug}/observability`（メンバーのみ、他は 404）→ `web/src/pages/observability/Observability.tsx`（`/w/:slug/observability`、上部のナビから）。画面は数え直さず、集計の値を表示だけする。

**Tech Stack:** Python 3.12 / SQLAlchemy async / PostgreSQL（`DISTINCT ON`）/ FastAPI / React + TS / vitest。

設計の根拠: design/2026-10-05-team-knowledge-registry.md 9 章（可観測性の行）・8 章・5.2。

## 指標の定義（正直な数字）

| 指標 | 定義 | 未計測（None）になる条件 |
| --- | --- | --- |
| 形式化率 | 今の bundle の spec のうち、スコープにそのプロジェクトを含むものの要求の表（`compiled.requirements`）で、`formalized_by` が 1 つ以上ある行 / 全行 | bundle が無い・当たる spec が無い・要求の行が 0 |
| 対応率 | 最新の完了した run（completed / awaiting_answer）の仕様側の義務（`behavior_id` が `policy:` / `knowledge:` で始まらない Finding の要求 ID）のうち、「対応なし」の UNKNOWN を 1 つも持たないもの / 義務の数 | run が無い・義務が 0 |
| UNKNOWN の内訳 | 同じ run の UNKNOWN の Finding を理由で分類（未対応・不完全な経路・解析の上限・判定の欠落・lock・その他） | run が無い |
| run の bundle の遅れ | 今の bundle の世代 − run の manifest の `bundle_generation` | どちらかが無い（bundle 外の run） |
| 取得の遅れ | トークン（無ければメンバー）×プロジェクトごとの直近 30 日の最後の取得の世代と、今の世代の差。失効していない sync トークンで取得の無いものは「未取得」 | 取得が無い |
| 規約の推移 | ワークスペースの直近 10 の完了した run について、規約ごとの VIOLATED / REVIEW / UNKNOWN / VERIFIED の件数。結果の無い run は None（0 と区別） | — |
| LLM 支援の安定度 | 同じ run の範囲の `assessment_outcome`（issue / no_issue / split）の件数。キャッシュの当たり率と確定の件数は任意の供給元（S70 の表）から。供給元が無ければ None | LLM 支援の Finding も供給元のデータも無い → 全体が None |

UNKNOWN の理由は Finding に構造化された欄が無い（契約を変えない）ため、`category`・`requirement_id`・メッセージの決まった言い回しで分類する。判別できないものは「その他」に入れ、どこかに無理に寄せない。

## Task 1: 表示の型（domain/observability.py）

- [ ] `UnknownReason`、`RuleTier`、`Ratio`、`UnknownBreakdown`、`RunSummary`、`ProjectHealth`、`FetchHealth`、`TrendRun`、`RulePoint`、`RuleTrend`、`LlmUsageStats`、`LlmStability`、`WorkspaceHealth` を定義（ロジックなし）
- [ ] 型の単体テスト（`Ratio` の分母 0 を拒否、既定値）→ commit

## Task 2: UNKNOWN の分類（純粋関数）

- [ ] 失敗するテスト: 各検査器の実際のメッセージ（trace_check / state_check / engine / expected / lock / solver_limit）から期待の理由
- [ ] `services/observability.py` に `unknown_reason(finding)`・`rule_tier(rule)`・`ratio(n, d)` を実装 → commit

## Task 3: 集計 `workspace_health`（統合テスト、実 DB）

- [ ] 固定データ: ワークスペース A（プロジェクト 2、bundle 世代 1・2、spec 2 件、run と findings、取得、sync トークン、規約の改訂）と B（混ざってはいけないデータ）
- [ ] 期待値: 形式化率 2/3、対応率、内訳、遅れ、取得の遅れと未取得、規約の推移（結果の無い run は None）、LLM の件数
- [ ] 空の状態: bundle も run も無い → 形式化率・対応率・内訳・latest_run が None、llm が None（0% にしない）
- [ ] 実装（クエリは固定数: projects、bundle、spec 改訂、最新 run は `DISTINCT ON`、findings は `run_id IN`、推移の run と規約の findings、取得は `DISTINCT ON`、トークン名・メンバー）→ commit

## Task 4: REST

- [ ] `GET /api/workspaces/{slug}/observability`: メンバー 200、非メンバー・存在しないワークスペースは 404、未認証 401
- [ ] ルータ登録、`openapi.json` 再生成 → commit

## Task 5: 画面

- [ ] `web/src/types/observability.ts`、`web/src/api/observability.ts`、`web/src/services/observability.ts`（率の表示「未計測」、理由のラベル、遅れの表示。vitest）
- [ ] `web/src/pages/observability/Observability.tsx`: リポジトリの表、取得の表、規約の推移（小さな棒）、LLM 支援のカード（None は「未計測」）
- [ ] `paths.observability`、App のルート、TopBar とアカウントメニューに「可観測性」
- [ ] `npm run lint && npm run typecheck && npm run build && npx vitest run` → commit

## Task 6: 仕上げ

- [ ] `make lint` / `make typecheck` / `make test`（DB つき）を緑に
- [ ] 報告書
