import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { listArtifactRevisions } from '@/api/knowledge'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { ApprovalPanel } from '@/components/knowledge/ApprovalPanel'
import { ConflictDialog } from '@/components/knowledge/ConflictDialog'
import { DiffView } from '@/components/knowledge/DiffView'
import { KnowledgeNav } from '@/components/knowledge/KnowledgeNav'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { LoadingState } from '@/components/LoadingState'
import { cn } from '@/lib/utils'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useSpecPreview } from '@/hooks/useSpecPreview'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { formatDateTime } from '@/services/format'
import { KIND_LABELS, STATUS_LABELS, parseSection, revisionText } from '@/services/knowledge'
import { paths } from '@/services/paths'
import { countErrors } from '@/services/specPreview'
import type { ArtifactKind, ArtifactRevision, SpecPreviewRequest } from '@/types/knowledge'
import type { SpecSideDiffProps } from '@/types/specEditor'

const STATUS_TONE = { draft: 'rev', active: 'ok' } as const

/** 改訂の一覧・前の改訂との差分・承認（設計 9 章）。 */
export function KnowledgeRevisions() {
  const { section, id } = useParams<{ section: string; id: string }>()
  const workspace = useCurrentWorkspace()
  const kind = parseSection(section)
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  if (!kind || !id) return <NotFoundPanel title="この知識は見つかりません" />
  return <RevisionsBody key={`${workspace.slug}/${id}`} slug={workspace.slug} kind={kind} id={id} />
}

function specRequest(rev: ArtifactRevision | null): SpecPreviewRequest | null {
  if (!rev || rev.content.kind !== 'spec') return null
  return { markdown: rev.content.markdown, domain: rev.content.domain, legacy_ir: rev.content.legacy_ir }
}

function RevisionsBody({ slug, kind, id }: { slug: string; kind: ArtifactKind; id: string }) {
  const [revisions, setRevisions] = useState<ArtifactRevision[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const [conflict, setConflict] = useState(false)
  const { projects } = useWorkspaceProjects(slug)

  useEffect(() => {
    let alive = true
    listArtifactRevisions(slug, id)
      .then((r) => alive && setRevisions(r))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError && err.status === 404 ? 'この知識は見つかりません' : err instanceof ApiError ? err.message : '改訂を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, id, attempt])

  const reload = useCallback(() => {
    setError(null)
    setRevisions(null)
    setSelected(null)
    setConflict(false)
    setAttempt((n) => n + 1)
  }, [])

  const names = useMemo(() => new Map((projects ?? []).map((p) => [p.id, p.repo_full_name])), [projects])
  const list = revisions ?? []
  const index = selected === null ? 0 : Math.max(0, list.findIndex((r) => r.revision === selected))
  const current = list[index] ?? null
  const previous = list[index + 1] ?? null
  const draft = list.find((r) => r.status === 'draft') ?? null

  // 仕様は正規形と IR も比べる（同じ parser で前後を変換する）
  const after = useSpecPreview(slug, specRequest(current))
  const before = useSpecPreview(slug, specRequest(previous))
  const draftPreview = useSpecPreview(slug, specRequest(draft))

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (!revisions) return <LoadingState message="改訂を取得しています…" />
  if (revisions.length === 0) return <NotFoundPanel title="改訂がありません" />

  const name = revisions[0].name
  return (
    <div className="space-y-6">
      <header className="min-w-0 space-y-3">
        <KnowledgeNav slug={slug} current={kind} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="min-w-0 truncate text-2xl font-bold tracking-tight">
            {KIND_LABELS[kind]} <span className="font-mono">{name}</span> の改訂
          </h1>
          <Link to={paths.knowledgeItem(slug, kind, id)} className="ml-auto text-sm text-primary underline-offset-4 hover:underline">
            エディタを開く
          </Link>
        </div>
      </header>

      <ApprovalPanel
        slug={slug}
        draft={draft}
        dirty={false}
        previewErrors={countErrors(draftPreview.preview?.parse_result.diagnostics ?? [])}
        previewPending={kind === 'spec' && !draftPreview.preview && !draftPreview.error}
        previewFailed={kind === 'spec' && !!draftPreview.error}
        onApproved={reload}
        onConflict={() => setConflict(true)}
      />

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Panel className="h-fit p-3">
          <PanelTitle className="px-2 pb-2">改訂（新しい順）</PanelTitle>
          <ul className="space-y-1">
            {revisions.map((r, i) => (
              <li key={r.revision}>
                <button
                  type="button"
                  aria-pressed={i === index}
                  onClick={() => setSelected(r.revision)}
                  className={cn('w-full rounded-md px-2 py-2 text-left text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none', i === index && 'bg-muted')}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">改訂 {r.revision}</span>
                    {r.status === 'superseded' ? (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-ink-3">{STATUS_LABELS[r.status]}</span>
                    ) : (
                      <ToneBadge tone={STATUS_TONE[r.status]}>{STATUS_LABELS[r.status]}</ToneBadge>
                    )}
                    {r.self_approved && <ToneBadge tone="rev">自己承認</ToneBadge>}
                  </span>
                  <span className="mt-1 block text-xs text-ink-3">
                    作成 <span className="font-mono break-all">{r.author}</span> · {formatDateTime(r.created_at)}
                  </span>
                  {r.approver && (
                    <span className="block text-xs text-ink-3">
                      承認 <span className="font-mono break-all">{r.approver}</span> · {formatDateTime(r.approved_at)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        {current && (
          <div className="min-w-0 space-y-6">
            <Panel className="space-y-3">
              <PanelTitle>
                改訂 {current.revision} {previous ? `と改訂 ${previous.revision} の差分` : '（最初の改訂）'}
              </PanelTitle>
              <p className="font-mono text-xs break-all text-ink-3">content_hash {current.content_hash}</p>
              <DiffView label="本文の差分" before={previous ? revisionText(previous.content, names) : ''} after={revisionText(current.content, names)} />
            </Panel>
            {kind === 'spec' && (
              <>
                <Panel className="space-y-3">
                  <PanelTitle>正規形の差分</PanelTitle>
                  <p className="text-xs text-ink-3">IR から作った正規形どうしを比べます。見た目だけの違い（空白・コメント・別名）は消え、意味の違いだけが残ります。</p>
                  <SpecSideDiff label="正規形の差分" previous={!!previous} before={before.preview?.canonical} after={after.preview?.canonical} />
                </Panel>
                <Panel className="space-y-3">
                  <PanelTitle>IR の差分</PanelTitle>
                  <p className="text-xs text-ink-3">今の parser で前後の本文を変換した IR（JSON）を比べます。</p>
                  <SpecSideDiff
                    label="IR の差分"
                    previous={!!previous}
                    before={before.preview?.parse_result.compiled ? irText(before.preview.parse_result.compiled) : before.preview ? null : undefined}
                    after={after.preview?.parse_result.compiled ? irText(after.preview.parse_result.compiled) : after.preview ? null : undefined}
                  />
                </Panel>
              </>
            )}
          </div>
        )}
      </div>
      <ConflictDialog open={conflict} ownText="" onReload={reload} onClose={() => setConflict(false)} />
    </div>
  )
}

/** source_hash は本文の空白でも変わるので、IR の比較からは外す（ir_hash は意味のハッシュ）。 */
function irText(compiled: Record<string, unknown>): string {
  const { source_hash: _ignored, ...rest } = compiled
  return JSON.stringify(rest, null, 2)
}

/** 仕様の正規形・IR の差分。変換中・変換できないときはそう書く。 */
function SpecSideDiff({ label, previous, before, after }: SpecSideDiffProps) {
  if (after === undefined || (previous && before === undefined)) return <p className="text-sm text-ink-3">変換しています…</p>
  if (after === null || (previous && before === null)) {
    return <p className="text-sm text-unk">どちらかの改訂の診断にエラーがあり、変換できません（本文の差分を見てください）。</p>
  }
  return <DiffView label={label} before={previous ? (before ?? '') : ''} after={after} />
}
