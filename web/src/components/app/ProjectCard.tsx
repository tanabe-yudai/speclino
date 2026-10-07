import { Link } from 'react-router-dom'
import { HealthRing } from '@/components/project/HealthRing'
import { formatDateTime, runTargetLabel } from '@/services/format'
import { STATE_META, TILE_ORDER } from '@/services/health'
import { paths } from '@/services/paths'
import type { ProjectSummary } from '@/types/project'
import { GateBadge } from './GateBadge'
import { ToneBadge } from './ToneBadge'

/**
 * 1 つのプロジェクトのカード（健康状態・件数・最新の検査とゲート）。
 * 広いカードでは 3 つの塊（名前 / 状態 / 最新の検査）を横に並べ、狭いと折り返して縦に積む。
 */
export function ProjectCard({ slug, project }: { slug: string; project: ProjectSummary }) {
  const [, name] = project.repo_full_name.split('/')
  const run = project.latest_run
  return (
    <Link to={paths.project(slug, project.repo_full_name)} className="row flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3 p-5">
      <HealthRing counts={project.status_counts} />
      <div className="min-w-[8rem] flex-1">
        <p className="truncate font-semibold">{name}</p>
        <p className="mt-0.5 truncate font-mono text-xs text-ink-3">{project.repo_full_name}</p>
      </div>
      <div className="min-w-[10rem] flex-1 text-xs text-ink-2">
        {project.status === 'unavailable' ? (
          <p className="text-ink-3">最新の検査の成果物を読めません</p>
        ) : project.state && project.status_counts ? (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <ToneBadge tone={STATE_META[project.state].tone}>{STATE_META[project.state].label}</ToneBadge>
            {TILE_ORDER.filter((k) => k !== 'ok' && (project.status_counts?.[k] ?? 0) > 0).map((k) => (
              <span key={k} className="whitespace-nowrap">
                {STATE_META[k].segment} {project.status_counts?.[k]}
              </span>
            ))}
          </p>
        ) : (
          <p className="text-ink-3">まだ検査されていません</p>
        )}
      </div>
      {run && (
        <div className="flex shrink-0 items-center gap-3 text-xs text-ink-3">
          <span className="whitespace-nowrap">
            <span className="block font-medium text-ink-2">{runTargetLabel(run.pr_number)}</span>
            <span className="block">{formatDateTime(run.created_at)}</span>
          </span>
          {run.gate_status && <GateBadge status={run.gate_status} />}
        </div>
      )}
    </Link>
  )
}
