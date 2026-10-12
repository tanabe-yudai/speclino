import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ToneBadge } from '@/components/app/ToneBadge'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { FindingCard } from '@/components/FindingCard'
import { LoadingState } from '@/components/LoadingState'
import { cn } from '@/lib/utils'
import { githubBlobUrl } from '@/services/github'
import { APPROVAL_LABELS, systemRuleTitle } from '@/services/labels'
import { plainTerms } from '@/services/displayText'
import { paths } from '@/services/paths'
import type { Finding } from '@/types/finding'
import type { ProjectRun, RequirementView } from '@/types/project'

/** 開いて根拠を見られる判定（違反・未検証・要確認）。 */
const OPENABLE = new Set(['VIOLATED', 'UNKNOWN', 'REVIEW'])

/**
 * 要求 1 行。違反・未検証・要確認は開くと、その要求の findings（反例つき）と仕様の該当行へのリンク。
 * 要求を定めた SpecLino の仕様が分かれば（artifact_id）、閉じた行からも仕様のエディタへ移れる。
 */
export function RequirementRow({
  slug,
  req,
  run,
  repo,
  findings,
  onOpen,
}: {
  slug: string
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

  // 題があれば主な行に、ID は下の行に小さく（題が無ければ ID を主な行に）
  const heading = req.title ? plainTerms(req.title) : (systemRuleTitle(req.id) ?? req.id)
  const summary = (
    <>
      <span className="min-w-0 flex-1 text-sm break-words">{heading}</span>
      <span className="flex flex-wrap items-center gap-2">
        {req.removed && <ToneBadge tone="rev">削除</ToneBadge>}
        {req.approval !== 'approved' && <ToneBadge tone="rev">{APPROVAL_LABELS[req.approval]}</ToneBadge>}
        <span className="text-xs text-ink-3">テスト {req.tests_declared}</span>
        {req.verdict ? <VerdictBadge verdict={req.verdict} /> : <span className="text-xs text-ink-3">未検証</span>}
      </span>
    </>
  )

  // 題は ID とバッジ（と開閉の矢印）の下の行に、省略せず折り返す（横に並べるとスマホで数文字しか見えない）
  const title = heading === req.id ? null : <code className="w-full font-mono text-xs text-ink-3 break-all">{req.id}</code>

  const specLink = req.artifact_id ? (
    <Link className="text-xs text-primary hover:underline" to={paths.knowledgeItem(slug, 'spec', req.artifact_id, { from: repo })}>
      SpecLino の仕様を開く
    </Link>
  ) : null

  if (!openable) {
    return (
      <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
        {summary}
        {title}
        {specLink}
      </li>
    )
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
        {title}
      </button>
      {open && (
        <div className="space-y-3 px-4 pb-4">
          {mine === null ? (
            <LoadingState message="根拠を取得しています…" />
          ) : mine.length === 0 ? (
            <p className="text-sm text-ink-3">この要求の根拠を取得できませんでした。検査の詳細で確認してください。</p>
          ) : (
            mine.map((f) => <FindingCard key={f.id} finding={f} title={req.title} repoFullName={repo} headSha={run.head_sha} />)
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
            {specLink}
            <Link className="text-primary hover:underline" to={paths.run(slug, run.id)}>
              検査の詳細
            </Link>
          </p>
        </div>
      )}
    </li>
  )
}
