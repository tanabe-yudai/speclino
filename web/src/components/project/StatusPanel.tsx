import { Link } from 'react-router-dom'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { formatDateTime, shortSha } from '@/services/format'
import { explainGate, summarizePolicies } from '@/services/gateExplain'
import { githubPrUrl } from '@/services/github'
import { paths } from '@/services/paths'
import type { Verdict } from '@/types/finding'
import type { ProjectOverview } from '@/types/project'
import { HealthRing } from './HealthRing'

const ORDER: Verdict[] = ['VIOLATED', 'REVIEW', 'UNKNOWN', 'VERIFIED_IN_SCOPE']

/** 状態: 健康状態のリング、4 値の件数、未解析・未承認、最新の検査（design 5.1-1）。 */
export function StatusPanel({ overview }: { overview: ProjectOverview }) {
  const run = overview.run
  const counts = overview.counts
  const policies = summarizePolicies(overview.policies)
  const gateNote = explainGate(run?.gate_status ?? null, policies)
  return (
    <Panel className="space-y-5">
      {/* スマホではリングを上、件数のタイルを下の全幅に（横に詰めるとバッジがはみ出す） */}
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <div className="flex items-center gap-4 sm:flex-col sm:gap-1.5">
          {/* 内訳の円グラフ（色は下の件数のタイルと同じ）。中央は適合率 */}
          <HealthRing size="lg" counts={counts} unanalyzed={overview.unanalyzed} />
          <p className="text-xs text-ink-3">要求の判定の内訳</p>
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-xs font-semibold text-ink-3">要求の判定</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {ORDER.map((v) => (
              <div key={v} className="min-w-0 rounded-lg bg-secondary px-3 py-2">
                <VerdictBadge verdict={v} className="max-w-full whitespace-normal" />
                <p className="mt-1 text-xl font-bold">{counts?.[v] ?? 0}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2">
        <span>未解析 {overview.unanalyzed}</span>
        <span>未承認 {overview.unapproved}</span>
        <span>
          規約の違反 {policies.violated} 件{policies.waived > 0 && `（うち例外で免除 ${policies.waived} 件）`}
        </span>
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
          {gateNote && <span className="w-full text-xs text-ink-2 sm:order-last">{gateNote}</span>}
          <Link className="ml-auto text-primary hover:underline" to={paths.run(run.id)}>
            検査の詳細
          </Link>
        </div>
      )}
    </Panel>
  )
}
