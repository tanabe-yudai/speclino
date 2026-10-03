import { Link } from 'react-router-dom'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { formatDateTime, shortSha } from '@/services/format'
import { STATE_META, TILE_ORDER } from '@/services/health'
import { explainGate, summarizePolicies } from '@/services/gateExplain'
import { githubPrUrl } from '@/services/github'
import { paths } from '@/services/paths'
import type { ProjectOverview } from '@/types/project'
import { HealthRing } from './HealthRing'


/** 状態: 状態の見出しと円グラフ（要求と規約を合わせた 5 区分）、件数、最新の検査（design 5.1-1）。 */
export function StatusPanel({ overview }: { overview: ProjectOverview }) {
  const run = overview.run
  const sc = overview.project.status_counts
  const state = overview.project.state
  const policies = summarizePolicies(overview.policies)
  const gateNote = explainGate(run?.gate_status ?? null, policies)
  return (
    <Panel className="space-y-5">
      {/* 状態の見出し・円グラフ・件数のタイルは同じ 5 区分（要求と規約を合わせた status_counts）から作る */}
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <div className="flex items-center gap-4 sm:flex-col sm:gap-2">
          <HealthRing size="lg" counts={sc} />
          {state ? (
            <ToneBadge tone={STATE_META[state].tone}>{STATE_META[state].label}</ToneBadge>
          ) : (
            <span className="text-xs text-ink-3">判定なし</span>
          )}
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-xs font-semibold text-ink-3">要求と規約の判定</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {TILE_ORDER.map((k) => (
              <div key={k} className="min-w-0 rounded-lg bg-secondary px-3 py-2">
                <ToneBadge tone={STATE_META[k].tone} className="max-w-full whitespace-normal">
                  {STATE_META[k].segment}
                </ToneBadge>
                <p className="mt-1 text-xl font-bold">{sc?.[k] ?? 0}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2">
        <span>未承認の要求 {overview.unapproved}</span>
        {policies.exempted > 0 && <span>免除の最短の期限 {policies.earliestExpiry ?? '—'}</span>}
      </p>
      <p className="text-xs text-ink-3">
        未検証には未解析の要求を含みます。免除中は、期限付きの例外で違反を免除している規約です。適合は、検査器が確認した範囲（検査の詳細の「保証範囲」）での適合です。
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
