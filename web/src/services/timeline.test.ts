import { describe, expect, it } from 'vitest'
import { describeEvent } from './timeline'

describe('describeEvent', () => {
  it('状態の遷移は日本語の 1 行の要約にする', () => {
    expect(describeEvent('state', { trigger: 'pull_request.opened', exec_state: 'queued', superseded: 0 }).summary).toBe(
      '待機中（きっかけ: PR が開かれた）',
    )
    expect(describeEvent('state', { exec_state: 'running' }).summary).toBe('実行中')
    expect(
      describeEvent('state', { gate: 'pass_with_exceptions', error: null, publish: 'publish', exec_state: 'completed' })
        .summary,
    ).toBe('完了 · ゲート: 例外付き通過 · GitHub に反映')
    expect(describeEvent('state', { exec_state: 'failed', error: '入力不正: x', publish: 'skip' }).summary).toBe(
      '実行失敗 · GitHub に反映しない',
    )
  })

  it('中身は「項目名: 値」で、既知の項目は日本語・値も読める形・空の値は出さない', () => {
    const d = describeEvent('state', { exec_state: 'failed', error: '入力不正: x', publish: 'skip', superseded: 0 })
    expect(d.fields).toEqual([
      { label: '状態', value: '実行失敗' },
      { label: 'エラー', value: '入力不正: x' },
      { label: 'GitHub への反映', value: '反映しない' },
    ])
  })

  it('ツールの記録も読める形にし、知らない項目は名前のまま出す', () => {
    const d = describeEvent('tool.test_run', {
      rule_id: 'R-TOOL-01',
      runner: 'cloud_run_sandbox',
      exit_code: 1,
      timed_out: false,
      statuses: { 't::a': 'failed', 't::b': 'passed' },
      extra_key: [1, 2],
    })
    expect(d.fields).toEqual([
      { label: '要求', value: 'R-TOOL-01' },
      { label: '実行環境', value: 'cloud_run_sandbox' },
      { label: '終了コード', value: '1' },
      { label: '時間切れ', value: 'いいえ' },
      { label: 'テストの結果', value: 't::a: failed / t::b: passed' },
      { label: 'extra_key', value: '1, 2' },
    ])
  })
})
