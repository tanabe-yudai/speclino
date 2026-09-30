/** 要求の一覧の絞り込みとまとめ方（design 5.1）。 */

import type { RequirementFilter, RequirementGroup, RequirementView } from '@/types/project'

export const REQUIREMENT_FILTERS: RequirementFilter[] = ['all', 'violated', 'unknown', 'review', 'unapproved']

export function matchesFilter(r: RequirementView, f: RequirementFilter): boolean {
  switch (f) {
    case 'all':
      return true
    case 'violated':
      return r.verdict === 'VIOLATED'
    case 'unknown':
      return r.verdict === 'UNKNOWN' || r.verdict === null
    case 'review':
      return r.verdict === 'REVIEW'
    case 'unapproved':
      return r.approval !== 'approved'
  }
}

export function filterCounts(reqs: RequirementView[]): Record<RequirementFilter, number> {
  return Object.fromEntries(
    REQUIREMENT_FILTERS.map((f) => [f, reqs.filter((r) => matchesFilter(r, f)).length]),
  ) as Record<RequirementFilter, number>
}

export function groupBySpec(reqs: RequirementView[]): RequirementGroup[] {
  const groups = new Map<string, RequirementGroup>()
  for (const r of reqs) {
    const key = r.spec_path ?? r.behavior_id
    let g = groups.get(key)
    if (!g) {
      g = { key, specPath: r.spec_path, behaviorId: r.behavior_id, items: [] }
      groups.set(key, g)
    }
    g.items.push(r)
  }
  return [...groups.values()]
}
