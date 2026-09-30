import { describe, expect, it } from 'vitest'
import { filterCounts, groupBySpec, matchesFilter } from './requirements'
import type { RequirementView } from '@/types/project'

function req(id: string, over: Partial<RequirementView>): RequirementView {
  return {
    id, behavior_id: 'b', title: null, spec_path: null, line_start: null, line_end: null,
    approval: 'approved', tests_declared: 0, verdict: 'VERIFIED_IN_SCOPE', finding_ids: [], removed: false, ...over,
  }
}

describe('requirements', () => {
  const reqs = [
    req('V', { verdict: 'VIOLATED', spec_path: 'specs/a.md' }),
    req('K', { verdict: 'UNKNOWN', spec_path: 'specs/a.md' }),
    req('N', { verdict: null, behavior_id: 'other' }),
    req('Q', { verdict: 'REVIEW', approval: 'stale', spec_path: 'specs/b.md' }),
  ]

  it('未検証の絞り込みは未解析（判定なし）も含む', () => {
    expect(reqs.filter((r) => matchesFilter(r, 'unknown')).map((r) => r.id)).toEqual(['K', 'N'])
  })

  it('未承認の絞り込みは承認切れも含む', () => {
    expect(reqs.filter((r) => matchesFilter(r, 'unapproved')).map((r) => r.id)).toEqual(['Q'])
  })

  it('件数', () => {
    expect(filterCounts(reqs)).toEqual({ all: 4, violated: 1, unknown: 2, review: 1, unapproved: 1 })
  })

  it('仕様ファイルごと（無ければ behavior ごと）に、出てきた順でまとめる', () => {
    expect(groupBySpec(reqs).map((g) => [g.key, g.items.map((r) => r.id)])).toEqual([
      ['specs/a.md', ['V', 'K']], ['other', ['N']], ['specs/b.md', ['Q']],
    ])
  })
})
