import { Link } from 'react-router-dom'
import { formatDateTime, runRowLabel, runTargetLabel, shortSha } from '@/services/format'
import { EXEC_STATE_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import type { ReviewRunSummary } from '@/types/run'
import { GateBadge } from './GateBadge'

/** 検査 1 件の行（対象・head・ゲート・実行状態・時刻）。行全体が詳細へのリンク。showRepo=false ならリポジトリ名を省く。 */
export function RunRow({ slug, run, showRepo = true }: { slug: string; run: ReviewRunSummary; showRepo?: boolean }) {
  return (
    <Link to={paths.run(slug, run.id)} className="row flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm">
      <span className="min-w-[9rem] flex-1 truncate font-medium">
        {showRepo ? runRowLabel(run.repo_full_name, run.pr_number) : runTargetLabel(run.pr_number)}
      </span>
      <code className="font-mono text-xs text-ink-3">{shortSha(run.head_sha)}</code>
      <GateBadge status={run.gate_status} />
      <span className="w-20 text-xs text-ink-2">{EXEC_STATE_LABELS[run.exec_state]}</span>
      <span className="text-xs text-ink-3">{formatDateTime(run.created_at)}</span>
    </Link>
  )
}
