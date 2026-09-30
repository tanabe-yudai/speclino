import { Link } from 'react-router-dom'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { formatDateTime, shortSha } from '@/services/format'
import { githubPrUrl } from '@/services/github'
import { healthRatio } from '@/services/health'
import { paths } from '@/services/paths'
import type { Verdict } from '@/types/finding'
import type { ProjectOverview } from '@/types/project'
import { HealthRing } from './HealthRing'

const ORDER: Verdict[] = ['VIOLATED', 'REVIEW', 'UNKNOWN', 'VERIFIED_IN_SCOPE']

/** 状態: 健康状態のリング、4 値の件数、未解析・未承認、最新の検査（design 5.1-1）。 */
export function StatusPanel({ overview }: { overview: ProjectOverview }) {
  const run = overview.run
  const counts = overview.counts
  return (
    <Panel className="space-y-5">
      <div className="flex flex-wrap items-center gap-5">
        <HealthRing size="lg" ratio={healthRatio(counts, overview.unanalyzed)} />
        <div className="grid min-w-0 flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
          {ORDER.map((v) => (
            <div key={v} className="rounded-lg bg-secondary px-3 py-2">
              <VerdictBadge verdict={v} />
              <p className="mt-1 text-xl font-bold">{counts?.[v] ?? 0}</p>
            </div>
          ))}
        </div>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2">
        <span>未解析 {overview.unanalyzed}</span>
        <span>未承認 {overview.unapproved}</span>
      </p>
      {run && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-4 text-sm">
          <span className="text-ink-3">最新の検査</span>
          {run.pr_number != null ? (
            <a className="text-primary hover:underline" href={githubPrUrl(overview.project.repo_full_name, run.pr_number)} target="_blank" rel="noreferrer">
              PR #{run.pr_number}
            </a>
          ) : (
            <span>手動</span>
          )}
          <code className="font-mono text-xs text-ink-3">{shortSha(run.head_sha)}</code>
          <span className="text-xs text-ink-3">{formatDateTime(run.created_at)}</span>
          <GateBadge status={run.gate_status} />
          <Link className="ml-auto text-primary hover:underline" to={paths.run(run.id)}>
            検査の詳細
          </Link>
        </div>
      )}
    </Panel>
  )
}
