# 仕様の承認を PR の承認とマージに寄せる（2026-10-04）

## 状態（2026-10-05）

- **PR の承認で仕様を承認する経路（PR の Approve・チェックの「仕様を承認」ボタン・コミットごとの承認の表・クローズ／再オープンでの失効）は取りやめた。**
  SpecLino を仕様の正本にし、仕様の承認は SpecLino の中の承認者フローに移すとユーザーが決めたため。
- 残して出したのは「承認者を GitHub のユーザー ID で照合する」部分だけ（4 章）。
  - 承認者の保存（設定画面・`set-pr-approvers`）でログイン名から ID を引いて `approver_ids` に保存し、PR コメントのコマンド（approve-spec / answer / attest）はコメントした人の ID で照合する。ID の無い設定では誰も承認者にならない（フェイルクローズ）。
  - 保存・補完で引くのは今そのログイン名を持つ人の ID なので、設定画面は承認者を `login（ID: 12345）` で表示し、`backfill-approver-ids` は既定で `login → id` を表示するだけにして `--yes` で保存する。
- 以下の本文は検討の記録として残す。

## 1. 目的

仕様（`specs/ir/` の要求）を変える PR は、**承認者がその PR のそのコミットを承認し、マージすること**で仕様の承認とする。
PR へのコメント（`/speclino approve-spec <ハッシュ>`）を打たなくても、承認が完了するようにする。

GitHub では、PR の作成者は自分の PR を Approve できない
（[GitHub Docs](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)）。
そのため、1 人で使う場合のために、PR の画面にある SpecLino のチェックに「仕様を承認」ボタンを付ける。

この仕組みは、どのユーザー・どのプロジェクトにも同じ規則で効く。承認者は、各プロジェクトの設定の承認者の一覧で決まる。

## 2. 承認になる操作

| 操作 | 誰が | 効く範囲 |
| --- | --- | --- |
| PR の **Approve**（`pull_request_review`、`submitted`、`state=approved`） | プロジェクトの承認者（他人の PR） | その PR の、同じ payload の base（`pull_request.base.sha`）とレビューしたコミット（`review.commit_id`）の組だけ |
| SpecLino のチェックの「**仕様を承認**」ボタン（`check_run`、`requested_action`） | プロジェクトの承認者（自分の PR でも押せる） | そのチェックを作った run の PR・base・head の組だけ |
| コメント `/speclino approve-spec <ハッシュ>` | プロジェクトの承認者 | 今のまま（ハッシュ単位で、プロジェクト全体に効く） |

- マージの後は、今までどおり main の仕様が信頼の基準になる（`base-trusted`）。
- 承認者でない人の Approve やボタン操作は、監査に残すだけで、承認にはしない。
- 承認者が出した PR を、PR を出したことだけで自動で承認することはしない。webhook の `sender` は push した人とは限らないため。

## 3. 効く範囲と失効

- PR とコミットごとの承認（`pr_head_approvals`）は、**その PR の、その base とコミットの組の検査にだけ**効く。プロジェクト全体のハッシュの承認には変換しない。閉じた PR や別の PR には効かない。
- コミットが変われば、その承認は効かない。新しいコミットで承認し直す。
- **base が変われば、その承認は効かない。** worker は run 自身の `base_sha` で承認を引く（`active_for(project, pr, base_sha, head_sha)`）。base を含めないと、main が進んだ後の run で、古い head に無い main の新しい要求が「削除」に見え、古い承認で覆われてしまう。
- Approve が取り消された（`pull_request_review`、`dismissed`）ら、そのレビューの承認を失効させる（`revoked_at`）。
  - 取り消しは**最終**。承認の行が無くても取り消しを記録し（`pr_review_dismissals`）、後から届いた同じレビューの Approve は承認にしない（webhook は届く順が前後する）。
- **PR を閉じた**（`pull_request`、`closed`。マージを含む）ら、その PR の承認をすべて失効させ、閉じた印（`pr_closures`）を付ける。
  - 閉じている間の Approve・ボタンは承認にしない。Approve は payload の `pull_request.state` が `open` であることも確かめる。
  - 開き直した（`reopened`）ら印を消す。閉じる前の承認は失効したまま（生き返らない）。承認し直すには、Approve し直すかボタンを押し直す（ボタンは同じ一意キーの失効済みの行を有効に戻す）。
- 検査のたびに、今の承認者の一覧で照らし直す。承認者から外された人の承認は効かない。
- 検査器に渡すときは、その run の追加・変更・削除の要求をすべて承認済みとして扱う。承認の記録には、出どころ（`pr_review` / `pr_check_action`）と承認者を残す。

## 4. 承認者の照合

- GitHub のログイン名は、改名の後に他人が取得できる（[GitHub Docs](https://docs.github.com/en/account-and-profile/concepts/username-changes)）。そのため、照合は **GitHub のユーザー ID** で行う。
- プロジェクトの設定で承認者を保存するとき、ログイン名から GitHub のユーザー ID を引き、一緒に保存する（`approver_ids`）。
  - ID を引けなかったログイン名は、保存を断る。
- 次の 3 つの経路すべてで、ID **だけ**で照合する。ログイン名では照合しない。
  - コメントでの承認・回答・証跡（`approve-spec` / `answer` / `attest`）
  - Approve
  - ボタン
- 既存の設定（ログイン名だけ・ID が足りない）では、ID の無い承認者は**承認できない**（フェイルクローズ）。ログイン名で照合すると、改名後の乗っ取りの穴が既存のプロジェクトに残るため。
  - 設定の API は `approver_ids_missing`（承認者がいるのに ID が無い・足りない）を返し、画面は「承認者の GitHub ユーザー ID が未登録です。保存し直してください」と出す。変更が無くても保存できる。
  - 管理 CLI `speclino workspace backfill-approver-ids --repo owner/name` で、保存済みのログイン名から ID を補える（公開 API で引く。admin の Cloud Run Job で実行する）。
  - `speclino workspace set-pr-approvers` も ID を引いて一緒に保存する。

## 5. webhook の扱い

- **Approve:** `review.commit_id` と、同じ payload の `pull_request.head.sha` が一致し、`pull_request.state` が `open` で、閉じた印も取り消しの記録も無いときだけ承認にする。base は同じ payload の `pull_request.base.sha`。
  - DB の「最新の run」と比べない。webhook は届く順が前後するため。
  - 一致しない（古いコミットへの Approve）なら、記録だけ残す。
- **ボタン:** 押されたチェックを作った SpecLino の run に結び付ける。チェックの `external_id`（作るときに run の ID を入れる）で run を引き、同じプロジェクトで、run に保存した `check_run_id` と `head_sha` が押されたチェックと一致することを確かめる。承認するのは**その run の `project_id / pr_number / base_sha / head_sha` だけ**。
  - 同じコミットを別の PR が共有していても、その PR は承認しない。`check_run.pull_requests[]` は食い違いの確認にだけ使う（run の PR が含まれていなければ断る）。
  - run の PR に閉じた印があれば断る。
  - 承認を記録したら、payload の PR の head が run の head と同じときだけ、同じ base とコミットで再検査の run を作る（head が進んでいれば記録だけ）。
- **PR を閉じた:** `pull_request.closed` で、その PR の承認をすべて失効させ、閉じた印を付ける。`reopened` で印を消す。
- 承認の処理は `services/pr_head_approvals.py` にまとめる。webhook のルートはイベントを取り出してそこを呼ぶだけ（ARCH-003）。
- **重複:** 同じレビュー・同じボタン操作の重複配送は、一意制約で 1 件にする。

## 6. データ

新しい表 `pr_head_approvals`（追加のマイグレーション）を作る。列は次のとおり。

| 列 | 中身 |
| --- | --- |
| `id` | |
| `project_id` | |
| `pr_number` | |
| `base_sha` | 承認した時点の PR の base |
| `head_sha` | |
| `source` | `pr_review` / `pr_check_action` |
| `github_user_id` | |
| `github_login` | |
| `review_id` | Approve のときだけ |
| `evidence_url` | |
| `created_at` | |
| `revoked_at` | |

- 一意制約は `(project_id, pr_number, base_sha, head_sha, source, github_user_id, review_id)`。
- 取り消しの記録 `pr_review_dismissals`（主キー `(project_id, review_id)`、`dismissed_at`）。
- 閉じた PR の印 `pr_closures`（主キー `(project_id, pr_number)`、`closed_at`）。閉じたら書き、開き直したら消す。
- 3 つの表は同じマイグレーション（0009）で作る。

あわせて、次の 2 つを変える。

- **プロジェクトの設定:** `approver_ids`（GitHub のユーザー ID の一覧）を足す。保存のとき、ログイン名から GitHub の API（`GET /users/{login}`）で ID を引く。設定の API の応答に `approver_ids_missing` を足す。
- **`ApprovalRecord.source`:** `pr_review` と `pr_check_action` を足す。

## 7. Check Run のボタン

- SpecLino のチェック（`SpecLino / gate`）は、承認の無い要求の変更（`stale_approval`）があるとき、`actions` に「仕様を承認」を付ける（GitHub の Check Run の `actions`、最大 3 個）。
- ボタンを押せるのはリポジトリに書き込める人だけ。そのうえで、SpecLino が承認者かを照らす。
- 要確認の説明（`next_steps`）は、次の順に並べる。
  1. 「承認者が PR を Approve するか、チェックの『仕様を承認』を押す」
  2. コメントでの承認

## 8. GitHub App の設定（ユーザーの作業）

- 権限 **Pull requests: Read** と **Checks: Read and write** を確かめる。
- イベント **Pull request review** と **Check run** を購読する（**Pull request** は既に購読している。`closed` もこれで届く）。
- デプロイの後、既存のプロジェクトの承認者の ID を補う（設定画面で保存し直すか、`backfill-approver-ids` を実行する）。補うまで、そのプロジェクトでは誰も承認できない。

## 9. 失敗したときの扱い

- 承認者の一覧が空なら、何も承認しない。
- 承認者の ID が取れない webhook は、承認しない側に倒す。
- 承認の対象の PR に run が無ければ、記録だけ残す。次の検査で使う。
- ボタンのチェックが SpecLino の run に結び付かなければ、承認しない。
- 承認者の ID が保存されていなければ、誰も承認しない。

## 10. テストと確認

- 次の場合に、承認になるか・ならないかを単体テストと統合テストで固定する。
  - Approve（承認者・承認者でない人・古いコミット）
  - ボタン（承認者・承認者でない人）
  - Approve の取り消し
  - 承認者から外した後
  - 改名（同じログイン名で別の ID）
  - 別の PR・閉じた PR への持ち越し
  - base が進んだ run（同じ head・別の base）
  - 取り消しが Approve より先に届く
  - 2 つの PR が共有するコミットのボタン（押したチェックの PR だけ）
  - PR を閉じる・開き直す・閉じた PR への承認
  - ID の無い既存の設定（誰も承認しない）
  - webhook の重複配送
- 本番: PR #100 で「仕様を承認」を押し、ゲートが通る。
