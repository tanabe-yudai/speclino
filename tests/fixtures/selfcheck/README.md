# selfcheck fixtures

このディレクトリは、SpecLino 自身を検査対象にする「検出テスト」用に凍結したスナップショットです。

## なぜ凍結しているか

デモ用のブランチは、わざと `src/speclino/agent/gateway.py` や `src/speclino/conformance/gate.py`
や `src/speclino/agent/tools/test_run.py` の実装、あるいは `specs/ir/*.spec-ir.json` を壊して
「SpecLino が違反を検出する」ことを見せます。

`tests/unit/test_self_verification.py` の検出テスト（`test_execute_before_permission_check_violates_r_tool_01`
など）は、正しい実装に対して「既知のアンカー文字列を置換して壊す」ことで違反シナリオを作ります。
これが現物（`src/speclino/...`・`specs/ir/...`）を直接読んでいると、デモがそのアンカー文字列や
Spec IR そのものを消したときに、アンカーが見つからず `assert mutated != src` で落ちたり、
Spec IR が読めず例外になったりして、**検出できているはずのテストまで巻き添えで壊れます**。

そこでこのディレクトリに、検出テストが「変異させる元」を固定したコピーとして持たせています。
デモが現物をどう壊しても、検出テストはここにあるコピーだけを読むので影響を受けません。

## ファイル

現物からそのままコピーしたもの（ファイル名は元と同じ）:

- `gateway.py` ← `src/speclino/agent/gateway.py`
- `gate.py` ← `src/speclino/conformance/gate.py`
- `run_freshness.py` ← `src/speclino/services/run_freshness.py`
- `test_run.py` ← `src/speclino/agent/tools/test_run.py`
- `tool_gateway.spec-ir.json` ← `specs/ir/tool_gateway.spec-ir.json`
- `gate_decide.spec-ir.json` ← `specs/ir/gate_decide.spec-ir.json`
- `run_freshness.spec-ir.json` ← `specs/ir/run_freshness.spec-ir.json`
- `test_run_decide.spec-ir.json` ← `specs/ir/test_run_decide.spec-ir.json`

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
cp specs/ir/tool_gateway.spec-ir.json specs/ir/gate_decide.spec-ir.json \
   specs/ir/run_freshness.spec-ir.json specs/ir/test_run_decide.spec-ir.json \
   tests/fixtures/selfcheck/
```

取り直したら、検出テスト（`pytest -m "not selfcheck"`）が新しいコピーでも通ることを確認します。
アンカー文字列を変えるリファクタリングをした場合は、検出テスト側の置換文字列も直す必要があります。
