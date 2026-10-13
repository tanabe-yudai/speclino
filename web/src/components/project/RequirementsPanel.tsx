import { useState } from 'react'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { cn } from '@/lib/utils'
import { REQUIREMENT_FILTER_LABELS } from '@/services/labels'
import { filterCounts, groupBySpec, matchesFilter, REQUIREMENT_FILTERS } from '@/services/requirements'
import type { Finding } from '@/types/finding'
import type { ProjectRun, RequirementFilter, RequirementView } from '@/types/project'
import { RequirementRow } from './RequirementRow'

/** 要求: 仕様ファイルごとにまとめ、判定と承認の状態で絞り込む。 */
export function RequirementsPanel({
  slug,
  requirements,
  unextracted,
  run,
  repo,
  findings,
  onOpen,
}: {
  slug: string
  requirements: RequirementView[]
  unextracted: string[]
  run: ProjectRun
  repo: string
  findings: Finding[] | null
  onOpen: () => void
}) {
  const [filter, setFilter] = useState<RequirementFilter>('all')
  const counts = filterCounts(requirements)
  const groups = groupBySpec(requirements.filter((r) => matchesFilter(r, filter)))

  return (
    <Panel className="space-y-4">
      <PanelTitle>要求</PanelTitle>
      <div className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1" role="group" aria-label="要求の絞り込み">
        {REQUIREMENT_FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={cn(
              'shrink-0 rounded-full px-3 py-1 text-sm transition-colors',
              filter === f ? 'bg-primary text-primary-foreground' : 'bg-secondary text-ink-2 hover:bg-accent',
            )}
          >
            {REQUIREMENT_FILTER_LABELS[f]} {counts[f]}
          </button>
        ))}
      </div>
      {groups.length === 0 ? (
        <p className="text-sm text-ink-3">該当する要求はありません。</p>
      ) : (
        groups.map((g) => (
          <section key={g.key} className="min-w-0">
            <h3 className="truncate font-mono text-xs text-ink-3">{g.specPath ?? g.behaviorId}</h3>
            <ul className="mt-2 divide-y overflow-hidden rounded-lg border">
              {g.items.map((r) => (
                <RequirementRow key={r.id} slug={slug} req={r} run={run} repo={repo} findings={findings} onOpen={onOpen} />
              ))}
            </ul>
          </section>
        ))
      )}
      {unextracted.length > 0 && (
        <p className="text-sm text-ink-2">
          抽出できなかった要求（未解析として数えています）:{' '}
          <span className="font-mono text-xs break-all">{unextracted.join(', ')}</span>
        </p>
      )}
    </Panel>
  )
}
