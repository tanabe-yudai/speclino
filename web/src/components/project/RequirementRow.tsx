import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ToneBadge } from '@/components/app/ToneBadge'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { FindingCard } from '@/components/FindingCard'
import { LoadingState } from '@/components/LoadingState'
import { cn } from '@/lib/utils'
import { githubBlobUrl } from '@/services/github'
import { APPROVAL_LABELS } from '@/services/labels'
import { paths } from '@/services/paths'
import type { Finding } from '@/types/finding'
import type { ProjectRun, RequirementView } from '@/types/project'

/** 開いて根拠を見られる判定（違反・未検証・要確認）。 */
const OPENABLE = new Set(['VIOLATED', 'UNKNOWN', 'REVIEW'])

/** 要求 1 行。違反・未検証・要確認は開くと、その要求の findings（反例つき）と仕様の該当行へのリンク。 */
export function RequirementRow({
  req,
  run,
  repo,
  findings,
  onOpen,
}: {
  req: RequirementView
  run: ProjectRun
  repo: string
  findings: Finding[] | null
  onOpen: () => void
}) {
  const [open, setOpen] = useState(false)
  const openable = req.verdict !== null && OPENABLE.has(req.verdict)
  const mine = findings?.filter((f) => req.finding_ids.includes(f.id)) ?? null

  function toggle() {
    if (!open) onOpen()
    setOpen(!open)
  }

  const summary = (
    <>
      <code className="min-w-0 font-mono text-xs break-all">{req.id}</code>
      <span className="min-w-0 flex-1 truncate text-sm">{req.title ?? '（題なし）'}</span>
      <span className="flex flex-wrap items-center gap-2">
        {req.removed && <ToneBadge tone="rev">削除</ToneBadge>}
        {req.approval !== 'approved' && <ToneBadge tone="rev">{APPROVAL_LABELS[req.approval]}</ToneBadge>}
        <span className="text-xs text-ink-3">テスト {req.tests_declared}</span>
        {req.verdict ? <VerdictBadge verdict={req.verdict} /> : <span className="text-xs text-ink-3">未解析</span>}
      </span>
    </>
  )

  if (!openable) {
    return <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">{summary}</li>
  }

  return (
    <li>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-left hover:bg-accent/50"
      >
        {summary}
        <ChevronDown className={cn('size-4 shrink-0 text-ink-3 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="space-y-3 px-4 pb-4">
          {mine === null ? (
            <LoadingState message="根拠を取得しています…" />
          ) : mine.length === 0 ? (
            <p className="text-sm text-ink-3">この要求の根拠を取得できませんでした。検査の詳細で確認してください。</p>
          ) : (
            mine.map((f) => <FindingCard key={f.id} finding={f} repoFullName={repo} headSha={run.head_sha} />)
          )}
          <p className="flex flex-wrap gap-x-4 text-sm">
            {req.spec_path && (
              <a
                className="text-primary hover:underline"
                href={githubBlobUrl(repo, run.head_sha, req.spec_path, req.line_start, req.line_end)}
                target="_blank"
                rel="noreferrer"
              >
                仕様の該当行
              </a>
            )}
            <Link className="text-primary hover:underline" to={paths.run(run.id)}>
              検査の詳細
            </Link>
          </p>
        </div>
      )}
    </li>
  )
}
