import { Link } from 'react-router-dom'
import { formatDateTime, shortSha } from '@/services/format'
import { EXEC_STATE_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import type { ReviewRunSummary } from '@/types/run'
import { GateBadge } from './GateBadge'

function targetLabel(run: ReviewRunSummary): string {
  const repo = run.repo_full_name ?? '(リポジトリ不明)'
  return run.pr_number != null ? `${repo}#${run.pr_number}` : repo
}

/** 検査 1 件の行（対象・head・ゲート・実行状態・時刻）。行全体が詳細へのリンク。 */
export function RunRow({ run }: { run: ReviewRunSummary }) {
  return (
    <Link
      to={paths.run(run.id)}
      className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm"
    >
      <span className="min-w-0 flex-1 truncate font-medium">{targetLabel(run)}</span>
      <code className="font-mono text-xs text-ink-3">{shortSha(run.head_sha)}</code>
      <GateBadge status={run.gate_status} />
      <span className="w-20 text-xs text-ink-2">{EXEC_STATE_LABELS[run.exec_state]}</span>
      <span className="text-xs text-ink-3">{formatDateTime(run.created_at)}</span>
    </Link>
  )
}
