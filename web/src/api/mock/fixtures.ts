/**
 * `VITE_MOCK=1` のときに使う fixture。design/api-sample.json（実際の API 応答サンプル）の
 * run / Finding（VIOLATED・VERIFIED_IN_SCOPE）/ events を核に、画面確認のため
 * REVIEW・UNKNOWN・他ゲート状態・実行中の run を補って構成している（バックエンド未実装）。
 */

import type { RunEvent } from '../../types/events'
import type { Finding } from '../../types/finding'
import type { ReviewRunDetail, ReviewRunSummary } from '../../types/run'

const REPO = 'example-org/demo-orders'

const RUN_FAIL_ID = '9871e2ff-4602-4382-b512-0bacd3006ae2'
const RUN_PASS_ID = 'b2c3d4e5-6789-4abc-9def-0123456789ab'
const RUN_EXCEPTION_ID = 'c3d4e5f6-789a-4bcd-8ef0-123456789abc'
const RUN_RUNNING_ID = 'd4e5f6a7-89ab-4cde-9f01-23456789abcd'
const RUN_QUEUED_ID = 'e5f6a7b8-9abc-4def-a012-3456789abcde'

const HEAD_SHA_FAIL = 'f81f51e1a284f80e21e3a9d71ff030d7d015f304'
const BASE_SHA_FAIL = 'd32730ac9271958039ef1c8bb699852b53522ec0'

const manifestFail = {
  schema_version: '0.1',
  run_id: RUN_FAIL_ID,
  created_at: '2026-09-22T08:10:40.304859Z',
  inputs: {
    repository: '/var/tmp/speclino-run/head/f81f51e1a284',
    head_commit: HEAD_SHA_FAIL,
    base_commit: BASE_SHA_FAIL,
    spec_content_hash: 'sha256:489a98b16ced6b1646b6868228fd14078b4f3a060271ffa37740932899379945',
    spec_ir_hash: 'sha256:f68976920f586c076d483e08ddeaa26090023951e8fd98d66703cca33281da3d',
    bindings_hash: 'sha256:08a2ad27363d05653e8ac74f362786af4fe611f0615d301739e2bc0317dcf4f4',
    domain_hash: 'sha256:ba3f260e87a6779e6d555e2cb1e2811c6d53d3d99c936f50ff5a3ce3865f3df5',
    contracts_hash: 'sha256:73aa135b3b2eb4972136d7b67838fcdf2c74840a07493bad26c5758bd7a720eb',
    policy_hash: null,
    code_ir_hash: null,
  },
  versions: {
    speclino: '0.1.0',
    analyzer: '0.1.0',
    engine: '0.1.0',
    solver: '5.1.0',
    settings_hash: null,
  },
  limits: { depth: 2, max_paths: 64, solver_timeout_ms: 500 },
  fail_on: ['VIOLATED', 'UNKNOWN', 'REVIEW'],
  mode: 'blocking' as const,
}

function runSummary(overrides: Partial<ReviewRunSummary> & Pick<ReviewRunSummary, 'id'>): ReviewRunSummary {
  return {
    repo_full_name: REPO,
    pr_number: 1,
    head_sha: HEAD_SHA_FAIL,
    base_sha: BASE_SHA_FAIL,
    trigger: 'pull_request',
    exec_state: 'completed',
    gate_status: 'fail',
    check_run_id: 55012345,
    artifacts_uri: `runs/${overrides.id}`,
    manifest: manifestFail,
    budget: { llm_calls: 0, tool_calls: 0 },
    created_at: '2026-09-22T08:10:40.304859Z',
    finished_at: '2026-09-22T08:10:52.118000Z',
    ...overrides,
  }
}

const findingViolated: Finding = {
  id: 'F-fcb4c85958e4',
  requirement_id: 'R-CANCEL-04',
  behavior_id: 'cancel_order',
  verdict: 'VIOLATED',
  category: 'missing_effect',
  severity: 'high',
  message:
    '経路 p4_204 で要求 R-CANCEL-04 に反する: 期待「inventory_restore_command を eq 1 回（引数 items が一致）」に対し「inventory_restore_command を 0 回」',
  expected: 'inventory_restore_command を eq 1 回（引数 items が一致）',
  actual: 'inventory_restore_command を 0 回',
  counterexample: {
    kind: 'symbolic',
    path_id: 'p4_204',
    assignments: {},
    description:
      '条件: ((before.actor.is_admin or before.actor.id == before.order.owner_id) and not(before.order.status != PENDING) and not(before.actor.is_admin)); 実装: inventory_restore_command を 0 回',
  },
  evidence_refs: ['p4_204'],
  assumptions: [],
  claim_scope: 'command_invocation',
  analysis_scope: '4 経路（呼出し深さ 2、経路上限 64、ソルバー 500ms、解析器 0.1.0）',
  code_refs: [
    {
      snapshot_id: HEAD_SHA_FAIL,
      requirement_id: null,
      symbol: 'examples.orders.app.service.cancel_order',
      path: 'examples/orders/app/service.py',
      line_start: 19,
      line_end: 27,
    },
  ],
  spec_refs: [
    {
      snapshot_id: 'demo-spec',
      requirement_id: 'R-CANCEL-04',
      symbol: null,
      path: null,
      line_start: null,
      line_end: null,
    },
  ],
  base_head_status: null,
  test_observation: null,
  next_steps: [],
}

const findingVerified: Finding = {
  id: 'F-e813ed1b8c1f',
  requirement_id: 'R-CANCEL-01',
  behavior_id: 'cancel_order',
  verdict: 'VERIFIED_IN_SCOPE',
  category: 'conformant',
  severity: 'info',
  message: '関連する 1 経路すべてで要求が成立（対応モデル内）',
  expected: 'outcome.status == 403',
  actual: null,
  counterexample: null,
  evidence_refs: [],
  assumptions: [],
  claim_scope: 'service_outcome',
  analysis_scope: '4 経路（呼出し深さ 2、経路上限 64、ソルバー 500ms、解析器 0.1.0）',
  code_refs: [
    {
      snapshot_id: HEAD_SHA_FAIL,
      requirement_id: null,
      symbol: 'examples.orders.app.service.cancel_order',
      path: 'examples/orders/app/service.py',
      line_start: 19,
      line_end: 20,
    },
  ],
  spec_refs: [
    {
      snapshot_id: 'demo-spec',
      requirement_id: 'R-CANCEL-01',
      symbol: null,
      path: null,
      line_start: null,
      line_end: null,
    },
  ],
  base_head_status: null,
  test_observation: null,
  next_steps: [],
}

const findingReview: Finding = {
  id: 'F-review00001',
  requirement_id: 'R-CANCEL-06',
  behavior_id: 'cancel_order',
  verdict: 'REVIEW',
  category: 'undocumented_behavior',
  severity: 'medium',
  message: '仕様に記載のない stock.release 呼び出しが実装に存在する（語彙対応が未確認）',
  expected: '仕様に記載された効果のみ',
  actual: 'stock.release を 1 回呼び出し（引数 items）',
  counterexample: null,
  evidence_refs: [],
  assumptions: ['stock.release は契約未登録のため語彙対応が未確認'],
  claim_scope: 'command_invocation',
  analysis_scope: '4 経路（呼出し深さ 2、経路上限 64、ソルバー 500ms、解析器 0.1.0）',
  code_refs: [
    {
      snapshot_id: HEAD_SHA_FAIL,
      requirement_id: null,
      symbol: 'examples.orders.app.service.cancel_order',
      path: 'examples/orders/app/service.py',
      line_start: 28,
      line_end: 31,
    },
  ],
  spec_refs: [
    {
      snapshot_id: 'demo-spec',
      requirement_id: 'R-CANCEL-06',
      symbol: null,
      path: null,
      line_start: null,
      line_end: null,
    },
  ],
  base_head_status: 'new',
  test_observation: null,
  next_steps: [
    'stock.release の契約を .speclino/contracts/ に登録する',
    'repo_search で呼び出し元の語彙対応を確認する',
  ],
}

const findingUnknown: Finding = {
  id: 'F-unknown00001',
  requirement_id: 'R-CANCEL-07',
  behavior_id: 'cancel_order',
  verdict: 'UNKNOWN',
  category: 'solver_limit',
  severity: 'medium',
  message: '経路が既定の呼出し深さ上限（depth=2）を超えるため未解析',
  expected: 'inventory_restore_command を eq 1 回',
  actual: null,
  counterexample: null,
  evidence_refs: [],
  assumptions: [],
  claim_scope: 'command_invocation',
  analysis_scope: '上限超過のため打ち切り（呼出し深さ 2、経路上限 64）',
  code_refs: [
    {
      snapshot_id: HEAD_SHA_FAIL,
      requirement_id: null,
      symbol: 'examples.orders.app.service._restore_stock_via_helpers',
      path: 'examples/orders/app/service.py',
      line_start: 40,
      line_end: 55,
    },
  ],
  spec_refs: [
    {
      snapshot_id: 'demo-spec',
      requirement_id: 'R-CANCEL-07',
      symbol: null,
      path: null,
      line_start: null,
      line_end: null,
    },
  ],
  base_head_status: 'unchanged',
  test_observation: null,
  next_steps: [
    'analyze_focus(R-CANCEL-07, depth=4) で再解析する',
    'ヘルパー経由の呼出しを直接呼び出しへ単純化する',
  ],
}

const findingVerifiedExtra: Finding = {
  ...findingVerified,
  id: 'F-verified00002',
  requirement_id: 'R-CANCEL-02',
  message: '関連する 2 経路すべてで要求が成立（対応モデル内）',
  expected: 'outcome.status == 409',
}

const findingWaived: Finding = {
  ...findingReview,
  id: 'F-waived00001',
  requirement_id: 'R-CANCEL-06',
  assumptions: [...findingReview.assumptions, '例外 EXC-001（期限内）により本ルールは免除'],
}

export const MOCK_RUNS: ReviewRunSummary[] = [
  runSummary({
    id: RUN_QUEUED_ID,
    pr_number: 5,
    exec_state: 'queued',
    gate_status: null,
    check_run_id: null,
    artifacts_uri: null,
    manifest: null,
    finished_at: null,
    created_at: '2026-09-22T09:40:00.000000Z',
  }),
  runSummary({
    id: RUN_RUNNING_ID,
    pr_number: 4,
    exec_state: 'running',
    gate_status: null,
    check_run_id: 55012348,
    finished_at: null,
    created_at: '2026-09-22T09:20:00.000000Z',
  }),
  runSummary({
    id: RUN_EXCEPTION_ID,
    pr_number: 3,
    exec_state: 'completed',
    gate_status: 'pass_with_exceptions',
    check_run_id: 55012347,
    created_at: '2026-09-22T09:00:00.000000Z',
    finished_at: '2026-09-22T09:00:14.000000Z',
  }),
  runSummary({
    id: RUN_PASS_ID,
    pr_number: 2,
    exec_state: 'completed',
    gate_status: 'pass',
    check_run_id: 55012346,
    created_at: '2026-09-22T08:30:00.000000Z',
    finished_at: '2026-09-22T08:30:11.000000Z',
  }),
  runSummary({ id: RUN_FAIL_ID }),
]

export const MOCK_RUN_DETAILS: Record<string, ReviewRunDetail> = {
  [RUN_FAIL_ID]: {
    ...MOCK_RUNS.find((r) => r.id === RUN_FAIL_ID)!,
    findings: [findingViolated, findingReview, findingUnknown, findingVerified],
  },
  [RUN_PASS_ID]: {
    ...MOCK_RUNS.find((r) => r.id === RUN_PASS_ID)!,
    findings: [findingVerified, findingVerifiedExtra],
  },
  [RUN_EXCEPTION_ID]: {
    ...MOCK_RUNS.find((r) => r.id === RUN_EXCEPTION_ID)!,
    findings: [findingVerified, findingWaived],
  },
  [RUN_RUNNING_ID]: {
    ...MOCK_RUNS.find((r) => r.id === RUN_RUNNING_ID)!,
    findings: [],
  },
  [RUN_QUEUED_ID]: {
    ...MOCK_RUNS.find((r) => r.id === RUN_QUEUED_ID)!,
    findings: [],
  },
}

export const MOCK_EVENTS: Record<string, RunEvent[]> = {
  [RUN_FAIL_ID]: [
    { seq: 1, ts: '2026-09-22T08:10:40.400000Z', type: 'state', payload: { trigger: 'pull_request', exec_state: 'queued', superseded: 0 } },
    { seq: 2, ts: '2026-09-22T08:10:41.100000Z', type: 'state', payload: { exec_state: 'running' } },
    { seq: 3, ts: '2026-09-22T08:10:45.200000Z', type: 'agent.start', payload: { agent: 'investigator_v1', reason: 'UNKNOWN/REVIEW が 2件残っている' } },
    { seq: 4, ts: '2026-09-22T08:10:45.400000Z', type: 'plan', payload: { summary: 'R-CANCEL-07 を analyze_focus(depth=4) で再解析する' } },
    { seq: 5, ts: '2026-09-22T08:10:45.900000Z', type: 'tool.policy_check', payload: { tool: 'analyze_focus', decision: 'allowed' } },
    { seq: 6, ts: '2026-09-22T08:10:47.300000Z', type: 'tool.analyze_focus', payload: { behavior_id: 'cancel_order', rule: 'R-CANCEL-07', depth: 4, result: 'UNKNOWN(limit) のまま' } },
    { seq: 7, ts: '2026-09-22T08:10:48.100000Z', type: 'tool.repo_search', payload: { query: 'stock.release', hits: 2 } },
    { seq: 8, ts: '2026-09-22T08:10:48.400000Z', type: 'audit', payload: { event: 'tool_execution', tool: 'repo_search', decision: 'allowed' } },
    { seq: 9, ts: '2026-09-22T08:10:51.700000Z', type: 'agent.end', payload: { outcome: 'escalation_incomplete', tool_calls: 3 } },
    { seq: 10, ts: '2026-09-22T08:10:52.118000Z', type: 'state', payload: { gate: 'fail', error: null, publish: 'publish', exec_state: 'completed' } },
  ],
  [RUN_PASS_ID]: [
    { seq: 1, ts: '2026-09-22T08:30:00.100000Z', type: 'state', payload: { trigger: 'pull_request', exec_state: 'queued', superseded: 0 } },
    { seq: 2, ts: '2026-09-22T08:30:01.000000Z', type: 'state', payload: { exec_state: 'running' } },
    { seq: 3, ts: '2026-09-22T08:30:11.000000Z', type: 'state', payload: { gate: 'pass', error: null, publish: 'publish', exec_state: 'completed' } },
  ],
  [RUN_EXCEPTION_ID]: [
    { seq: 1, ts: '2026-09-22T09:00:00.100000Z', type: 'state', payload: { trigger: 'pull_request', exec_state: 'queued', superseded: 0 } },
    { seq: 2, ts: '2026-09-22T09:00:01.000000Z', type: 'state', payload: { exec_state: 'running' } },
    { seq: 3, ts: '2026-09-22T09:00:14.000000Z', type: 'state', payload: { gate: 'pass_with_exceptions', error: null, publish: 'publish', exec_state: 'completed' } },
  ],
  [RUN_RUNNING_ID]: [
    { seq: 1, ts: '2026-09-22T09:20:00.100000Z', type: 'state', payload: { trigger: 'pull_request', exec_state: 'queued', superseded: 0 } },
    { seq: 2, ts: '2026-09-22T09:20:01.000000Z', type: 'state', payload: { exec_state: 'running' } },
  ],
  [RUN_QUEUED_ID]: [
    { seq: 1, ts: '2026-09-22T09:40:00.100000Z', type: 'state', payload: { trigger: 'pull_request', exec_state: 'queued', superseded: 0 } },
  ],
}

export const MOCK_REVIEW_MARKDOWN: Record<string, string> = {
  [RUN_FAIL_ID]: `## 一覧

- 対象: \`cancel_order\`
- 比較: \`${BASE_SHA_FAIL}\` → \`${HEAD_SHA_FAIL}\`
- 要求: 4件

| 判定 | 件数 |
| --- | --- |
| 適合（確認範囲内） | 1 |
| 違反 | 1 |
| 不明 | 1 |
| 要確認 | 1 |

- ゲート: **失敗** — 必須ルールが非成立: R-CANCEL-04, R-CANCEL-06, R-CANCEL-07
- 確認範囲: 4 経路（呼出し深さ 2、経路上限 64、ソルバー 500ms、解析器 0.1.0）
- 仮定: stock.release は契約未登録のため語彙対応が未確認

## 対応状況（PoC 17.3）

| 指標 | 件数 |
| --- | --- |
| 要求数 | 7 |
| 承認済み数 | 7 |
| 対応範囲数 | 6 |
| 判定済み数 | 4 |
| 未解析数 | 1 |

## 詳細

### R-CANCEL-04: 経路 p4_204 で要求 R-CANCEL-04 に反する

- 判定: VIOLATED（違反）／分類: missing_effect
- 根拠種別: symbolic
- 経路: p4_204
- 期待: inventory_restore_command を eq 1 回（引数 items が一致）
- 実装: inventory_restore_command を 0 回
- 仕様: R-CANCEL-04
- コード: examples.orders.app.service.cancel_order:19-27
`,
  [RUN_PASS_ID]: `## 一覧

- 対象: \`cancel_order\`
- 要求: 2件

| 判定 | 件数 |
| --- | --- |
| 適合（確認範囲内） | 2 |
| 違反 | 0 |
| 不明 | 0 |
| 要確認 | 0 |

- ゲート: **通過** — 必須ルールはすべて成立
`,
}
