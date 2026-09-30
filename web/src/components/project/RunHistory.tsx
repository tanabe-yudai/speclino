import { Link } from 'react-router-dom'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { formatDateTime, shortSha } from '@/services/format'
import { EXEC_STATE_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import type { ProjectRun } from '@/types/project'

/** 検査履歴: その project の run（新しい順）。行全体が詳細へのリンク（design 5.1-4）。 */
export function RunHistory({ runs }: { runs: ProjectRun[] }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>検査履歴</PanelTitle>
      {runs.length === 0 ? (
        <p className="text-sm text-ink-3">まだ検査がありません。</p>
      ) : (
        <div className="space-y-2">
          {runs.map((r) => (
            <Link key={r.id} to={paths.run(r.id)} className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium">{r.pr_number != null ? `PR #${r.pr_number}` : '手動'}</span>
              <code className="font-mono text-xs text-ink-3">{shortSha(r.head_sha)}</code>
              {r.gate_status ? (
                <GateBadge status={r.gate_status} />
              ) : (
                <span className="text-xs text-ink-2">{EXEC_STATE_LABELS[r.exec_state]}</span>
              )}
              <span className="text-xs text-ink-3">{formatDateTime(r.created_at)}</span>
            </Link>
          ))}
        </div>
      )}
    </Panel>
  )
}
