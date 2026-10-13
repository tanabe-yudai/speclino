# チームの知識レジストリ: 3 分の通し手順（ローカル）

この手順は手元で DB から作り直して通すもの（題材 `examples/shop`）。本番のデモ（このリポジトリ自身の仕様と
デモ PR #47・#45・#46）の見せ方は [docs/submission/video-script.md](submission/video-script.md) と
[docs/submission/form.md](submission/form.md) を参照。

題材は `examples/shop`（注文の確定・出荷・取消）。仕様は Mermaid の UML（クラス・シーケンス・状態遷移・
アクティビティ）で書き、SpecLino が決定的に IR へ変換して、PR のコードを 4 値（VERIFIED_IN_SCOPE /
VIOLATED / UNKNOWN / REVIEW）で判定する。skill と AGENTS.md の指示も同じ承認の流れで bundle に入り、
`speclino sync` で署名つきの lock とともに配られる。LLM 支援の規約は REVIEW しか出さず、確定は人が行う。

## 0. 準備（本番前に 1 回、約 1 分）

```sh
UV_NO_SYNC=0 uv sync --all-groups && (cd web && npm ci && npm run build)

# 空の DB（ポートは任意）
docker run -d --name speclino-shop-db -e POSTGRES_USER=speclino -e POSTGRES_PASSWORD=speclino \
  -e POSTGRES_DB=speclino -p 55432:5432 postgres:17-alpine
export SPECLINO_DATABASE_URL=postgresql+asyncpg://speclino:speclino@localhost:55432/speclino
uv run alembic upgrade head

# ワークスペース shop-team・プロジェクト example/shop・承認済みの仕様 / skill / 指示 / LLM 支援の規約、
# 検査対象の git リポジトリ（main と変異ごとの PR ブランチ）、main と 7 つの PR の検査結果を作る
uv run python scripts/seed_demo.py --repo-dir build/shop-repo
```

seed は main を検査したあと、main の LLM 支援の「指摘なし」を owner が確定した状態にしてから main を
検査し直す（必須の LLM 支援の規約は「指摘なし」でも人の確定までゲートを止めるため。チームが main の評価を
一度確かめた、という前提）。LLM 支援の規約 `SHOP-ERR-001` の単位は `OrderError` に渡したメッセージ
（`unit_kind: string_literal`、`call_names: [OrderError]`）。確定は単位の内容に結びつくので、PR で変わって
いないメッセージにはそのまま効き、PR で変わったメッセージは確定し直すまで要確認に残る。

`seed_demo.py` は LLM を呼ばない。LLM 支援の規約は「コードの文字列だけで答える決定的な偽物」
（`examples/shop/seed.py` の `ScriptedChecklistLlm`、モデル名 `scripted-checklist`）で評価し、その記録を DB に残す。
仕様・skill・指示は作者 `spec-author` が下書きし、owner の `judge`（画面の開発ログインの利用者）が承認する。

サーバー（web の画面も同じポートで配る）:

```sh
export SPECLINO_ENV=local SPECLINO_ROLE=all SPECLINO_AUTH_KIND=dev
export SPECLINO_CODE_HOST_KIND=local
export SPECLINO_LOCAL_REPOS="{\"example/shop\": \"$PWD/build/shop-repo\"}"
export SPECLINO_LOCAL_REPO_IDS='{"example/shop": 9007199254740991}'
# 記録と同じモデル名にすると、変わっていないコードは記録を使い LLM を呼ばない
export SPECLINO_LLM_POLICIES_MODEL=scripted-checklist SPECLINO_AGENT_ENABLED=false SPECLINO_SPEC_BOT_ENABLED=false
uv run uvicorn speclino.api.app:app --port 8080
```

ブラウザで `http://localhost:8080/login` →「開発用ログイン」。ワークスペース `shop-team` のホーム
（`/w/shop-team`）に入り、「やること」に承認待ち・ゲートが失敗の PR・知識が古いリポジトリが並ぶ。

## 1. 仕様を書く（0:00–1:00）

上部の「仕様」（`/w/shop-team/specs`）→「リポジトリ別の仕様」の `shop`。今の bundle は世代 4（仕様・skill・指示・規約の 4 回の承認）。

![知識の一覧](screenshots/knowledge-list.png)

シーケンス図の `alt` のガードを `before.order.quantity` から `before.order.qty` に書き換える。
クラス図に無い属性なので、その場で行と列つきの診断が出る（押すと本文のその行へ移る）。
自然言語のガード・未対応の Mermaid 構文も同じように必ずエラーになり、IR に入らない。

![ガードの誤りの診断](screenshots/knowledge-spec-diagnostic.png)

元に戻すと診断は 0 件になり、図のプレビューで 4 つの図がすべて描ける（Mermaid そのもので描くので、
GitHub でも同じ図が見える）。アクティビティ図の辺のラベルは `-->|"[e2] guard"|` と引用符で囲む。

![図のプレビュー](screenshots/knowledge-spec-preview.png)

説明を 1 文足して「下書きを保存」→「承認して有効にする」。承認者が見た改訂番号と content_hash を
サーバーが照合し、新しい bundle（世代 5）ができる。作者本人の承認は自己承認として印が残る。

![承認の確認](screenshots/knowledge-spec-approve.png)

## 2. skill と指示（1:00–1:20）

「スキル」（スマホでは「その他」→「スキル」）→ `shop-error-format`。`SKILL.md` と付属ファイルを同じ承認の流れで管理する。
`speclino sync` は `.claude/skills/` と `.agents/skills/`、`AGENTS.md` の管理区画、`.speclino/specs/` に書き出し、
署名つきの `.speclino/lock.json` を置く（`build/shop-repo` の main には seed が sync した結果が入っている）。

![skill のエディタ](screenshots/knowledge-skill.png)

## 3. PR を検査する（1:20–2:30）

`build/shop-repo` の `pr/<変異>` ブランチが PR。変異は `examples/shop/mutations/*.patch`（README に一覧）。
seed が検査済みなので、「検査」（`/w/shop-team/runs`）の一覧か、プロジェクトの画面の「検査」タブから開く
（seed の出力の `/runs/<id>` も `/w/shop-team/runs/<id>` に転送される）。

| PR | 変異 | 判定 |
| --- | --- | --- |
| #1 | 決済を呼ばない | `place#OrderService.place_order` が VIOLATED（反例つき） |
| #2 | 未決済でも出荷する | `status`（ship_order）が VIOLATED（PENDING → SHIPPED は図に無い） |
| #3 | 取消で返金も呼ぶ | `cancel#OrderService.cancel_order` が VIOLATED（閉じたアクティビティ図に無い呼出し） |
| #4 | 確保と決済の間に通知を挟む | 変化なし（シーケンス図は「この順で含む」。図に無い呼出しは許す） |
| #5 | `getattr` で決済を呼ぶ | `place#…` と `status`（place_order）が UNKNOWN（解析できないものを成立にしない） |
| #6 | PR で bindings の決済を通知に向ける | `KNOWLEDGE-BINDINGS-001` が REVIEW（判定は main の bindings で行う） |
| #7 | 例外のメッセージを短くする | LLM 支援の規約 `SHOP-ERR-001` が REVIEW（LLM の指摘あり、人の確定待ち） |

PR #1: 反例（`before.order.status = PENDING`、在庫と数量）と、どの経路で決済が呼ばれないかが出る。

![VIOLATED と反例](screenshots/knowledge-run-violated.png)

PR #6: bindings の変更は PR の head からは読まない。確定は GitHub 側で行う（承認者を GitHub のユーザー ID で照合するため）:
承認者が PR に引数なしの `/speclino attest` とコメントするか、PR を Approve すると、その PR の最新の検査の要確認を
まとめて確定する（ローカルの `example/shop` には GitHub が無いので、この画面では案内だけを見る）。

![bindings の変更の REVIEW](screenshots/knowledge-run-bindings-review.png)

PR #7: LLM 支援の規約は 3 回の評価の多数で「指摘あり」。verdict は REVIEW のままで、owner / approver が確定する。

![LLM 支援の REVIEW と確定のボタン](screenshots/knowledge-run-llm-review.png)

「問題なしとして確定」を押すと、同じ PR の再検査（trigger = attestation）に移る。確定した指摘はゲートを止めない。
判定は REVIEW のままだが、ゲートを止めない REVIEW（owner / approver が確定した指摘。LLM の「指摘なし」も
必須の規約では確定したものだけ）は「確定済み・情報」として要確認とは別に数える（件数・失敗の理由・一覧は、ゲートが決めた同じ集合 `cleared_ids` から作る）。
残る REVIEW は `KNOWLEDGE-LOCK-001`（1 で承認して世代 5 になったのに、ブランチの lock は世代 4 のまま）。
`speclino sync` で最新にすれば消える。

![再検査](screenshots/knowledge-run-recheck.png)

![確定済みの表示](screenshots/knowledge-run-recheck-attested.png)

## 4. 観測（2:30–3:00）

上部の「観測」（`/w/shop-team/observability`）: 今の bundle の世代、リポジトリごとの形式化率（要求の表のうち図に結びついた割合、4/5）と
検査の bundle の遅れ、規約ごとの推移、LLM 支援の安定度（指摘あり / なし / split、記録の当たり率、人の確定）。
測っていないものは「未計測」と出す。

![可観測性](screenshots/knowledge-observability.png)

![LLM 支援の安定度](screenshots/knowledge-observability-llm.png)

## ブラウザの通し確認（スクリーンショットの撮り直し）

上の画面は Playwright で撮っている（`web/e2e/knowledge.spec.ts`）。seed 直後の DB とサーバーに対して:

```sh
cd web && E2E_BASE_URL=http://localhost:8080 E2E_SHOTS_DIR=../docs/screenshots npx playwright test
```

仕様を承認して世代を進めるので、撮り直すときは DB を作り直して seed からやり直す
（`docker rm -f speclino-shop-db` → 準備の手順）。Chromium が無ければ `npx playwright install chromium`。

## テスト

- `tests/unit/examples/test_shop.py`: 仕様が診断 0 件で変換できること、変異ごとに上の表の判定だけが変わること（DB なし）
- `tests/integration/services/test_shop_bundle.py`: seed を実 DB で流し、worker の検査結果が上の表どおりであること
