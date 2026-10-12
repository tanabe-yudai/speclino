# selfcheck fixtures

このディレクトリは、SpecLino 自身を検査対象にする「検出テスト」用に凍結したスナップショットです。

## なぜ凍結しているか

デモ用のブランチは、わざと `src/speclino/agent/gateway.py` や `src/speclino/conformance/gate.py`
や `src/speclino/agent/tools/test_run.py` の実装を壊して
「SpecLino が違反を検出する」ことを見せます。

`tests/unit/test_self_verification.py` の検出テスト（`test_execute_before_permission_check_violates_r_tool_01`
など）は、正しい実装に対して「既知のアンカー文字列を置換して壊す」ことで違反シナリオを作ります。
これが現物（`src/speclino/...`）を直接読んでいると、デモがそのアンカー文字列を消したときに、
アンカーが見つからず `assert mutated != src` で落ちて、**検出できているはずのテストまで巻き添えで
壊れます**。

そこでこのディレクトリに、検出テストが「変異させる元」を固定したコピーとして持たせています。
デモが現物をどう壊しても、検出テストはここにあるコピーだけを読むので影響を受けません。

## ファイル

現物からそのままコピーしたソース（ファイル名は元と同じ）:

- `gateway.py` ← `src/speclino/agent/gateway.py`
- `gate.py` ← `src/speclino/conformance/gate.py`
- `run_freshness.py` ← `src/speclino/services/run_freshness.py`
- `test_run.py` ← `src/speclino/agent/tools/test_run.py`
検出テストの仕様（旧形式の Spec IR）。仕様の正本は SpecLino（ワークスペース）にあり、リポジトリには
`specs/ir` を置かないので、ここにあるものが唯一のコピー（下のドリフト検査の対象外）:

- `tool_gateway.spec-ir.json`（R-TOOL-01〜03）
- `gate_decide.spec-ir.json`（R-GATE-01〜03）
- `run_freshness.spec-ir.json`（R-RUN-01〜03）
- `test_run_decide.spec-ir.json`（R-TEST-01〜03）

語彙と対応は現物の `.speclino/domain.yaml`・`bindings.yaml` を使う。selfcheck の
`test_frozen_spec_ir_is_valid_against_current_vocabulary` と `test_gate_and_run_specs_are_valid_and_verified`
が、今の語彙で読めること・今のコードが適合することを確かめる。

## ドリフトはどのテストが検知するか

現物を正当な理由で変更した場合、このコピーは古くなります。
`tests/unit/test_self_verification.py::test_selfcheck_fixtures_match_live_source`
（`@pytest.mark.selfcheck`）が現物とこのディレクトリの内容を突き合わせ、差分があれば
「fixture が古い。意図した変更なら fixture を更新する」旨のメッセージで失敗します。

このテストは `selfcheck` マーカーなので `make test` / CI の `test (backend)` には含まれません
（`make selfcheck` / CI の `self-check` ジョブでのみ実行されます）。現物を変更したときは、
`make selfcheck` を一度手元で実行してドリフトが無いかを確認し、あれば取り直してください:

```sh
cp src/speclino/agent/gateway.py src/speclino/conformance/gate.py \
   src/speclino/services/run_freshness.py src/speclino/agent/tools/test_run.py \
   tests/fixtures/selfcheck/
```

取り直したら、検出テスト（`pytest -m "not selfcheck"`）が新しいコピーでも通ることを確認します。
アンカー文字列を変えるリファクタリングをした場合は、検出テスト側の置換文字列も直す必要があります。
