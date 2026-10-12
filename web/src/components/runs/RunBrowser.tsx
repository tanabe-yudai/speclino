import { useNavigate, useSearchParams } from 'react-router-dom'
import { Panel } from '@/components/app/Panel'
import { RunRow } from '@/components/app/RunRow'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { TabNav } from '@/components/shell/TabNav'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { useWorkspaceRuns } from '@/hooks/useWorkspaceRuns'
import { RUN_GATE_FILTER_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import { RUN_GATE_FILTERS, filterRuns, parseRunFilter } from '@/services/runList'
import type { RunFilter } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'

const ALL = 'all'

/**
 * 検査の一覧（新しい順）と絞り込み（ゲート・プロジェクト、URL のクエリ）。
 * project を渡すとそのプロジェクトで絞った一覧（プロジェクトの検査のタブ）になる。
 */
export function RunBrowser({ slug, project }: { slug: string; project?: ProjectSummary }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const parsed = parseRunFilter(params)
  const filter: RunFilter = project ? { ...parsed, project: project.repo_full_name } : parsed
  const { runs, error, retry } = useWorkspaceRuns(slug)
  const { projects } = useWorkspaceProjects(project ? undefined : slug)
  const listPath = (f: RunFilter) => (project ? paths.project(slug, project.repo_full_name, 'runs', f) : paths.runs(slug, f))
  const shown = runs ? filterRuns(runs, filter) : null

  return (
    <section className="space-y-4" aria-label="検査">
      <div className="flex flex-wrap items-center gap-3">
        <TabNav
          label="ゲートで絞る"
          current={filter.gate ?? ALL}
          items={[
            { id: ALL, label: 'すべて', to: listPath({ ...filter, gate: null }) },
            ...RUN_GATE_FILTERS.map((g) => ({ id: g, label: RUN_GATE_FILTER_LABELS[g], to: listPath({ ...filter, gate: g }) })),
          ]}
        />
        {!project && (
          <label className="flex items-center gap-2 text-sm text-ink-2">
            プロジェクト
            <select
              className="h-8 max-w-[14rem] rounded-md border bg-background px-2 text-sm"
              value={filter.project ?? ''}
              onChange={(e) => navigate(listPath({ ...filter, project: e.target.value || null }), { replace: true })}
            >
              <option value="">すべて</option>
              {(projects ?? []).map((p) => (
                <option key={p.id} value={p.repo_full_name}>
                  {p.repo_full_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && shown === null && <LoadingState message="検査の一覧を取得しています…" />}
      {shown !== null && shown.length === 0 && <Panel className="text-sm text-ink-2">条件に合う検査はありません。PR を開くと検査が始まります。</Panel>}
      {shown !== null && shown.length > 0 && (
        <div className="space-y-2">
          {shown.map((run) => (
            <RunRow key={run.id} slug={slug} run={run} showRepo={!project} />
          ))}
        </div>
      )}
    </section>
  )
}
