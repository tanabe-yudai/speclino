import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { getCurrentBundle, listArtifacts } from '@/api/knowledge'
import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { KnowledgeNav } from '@/components/knowledge/KnowledgeNav'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { formatDateTime } from '@/services/format'
import { KIND_LABELS, parseSection } from '@/services/knowledge'
import { paths } from '@/services/paths'
import type { ArtifactKind, ArtifactSummary, Bundle } from '@/types/knowledge'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** `/w/:slug/knowledge` は仕様の一覧へ。 */
export function KnowledgeIndex() {
  const { slug } = useParams<{ slug: string }>()
  return <Navigate to={paths.knowledge(slug ?? '')} replace />
}

/** 知識の一覧（種類ごと、設計 9 章）。状態・有効な改訂・下書きと、今の bundle の世代。 */
export function KnowledgeList() {
  const { section } = useParams<{ section: string }>()
  const workspace = useCurrentWorkspace()
  const kind = parseSection(section)
  useDocumentTitle(kind ? KIND_LABELS[kind] : '知識')
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  if (!kind) return <NotFoundPanel title="この種類の知識はありません" />
  return <ListBody key={workspace.slug} slug={workspace.slug} name={workspace.name ?? workspace.slug} kind={kind} />
}

interface Loaded {
  artifacts: ArtifactSummary[]
  bundle: Bundle | null
}

function ListBody({ slug, name, kind }: { slug: string; name: string; kind: ArtifactKind }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    Promise.all([listArtifacts(slug), getCurrentBundle(slug)])
      .then(([artifacts, bundle]) => alive && setLoaded({ artifacts, bundle }))
      .catch((err: unknown) => alive && setError(err instanceof ApiError ? err.message : '知識の一覧を取得できませんでした'))
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    setError(null)
    setLoaded(null)
    setAttempt((n) => n + 1)
  }, [])

  const label = KIND_LABELS[kind]
  const rows = (loaded?.artifacts ?? []).filter((a) => a.kind === kind).sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div className="space-y-6">
      <header className="min-w-0 space-y-3">
        <div>
          <p className="text-xs font-semibold tracking-widest text-primary uppercase">Knowledge</p>
          <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{name} の知識</h1>
          <p className="mt-1 text-sm text-ink-2">
            仕様・スキル・指示書は下書きを owner / approver が承認すると有効になり、新しい bundle として配信・検査に使われます。
          </p>
        </div>
        <KnowledgeNav slug={slug} current={kind} />
      </header>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && !loaded && <LoadingState message="知識の一覧を取得しています…" />}
      {loaded && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-ink-2">
              {loaded.bundle
                ? `今の bundle: 世代 ${loaded.bundle.generation}（${formatDateTime(loaded.bundle.created_at)}、${loaded.bundle.members.length} 件）`
                : 'まだ bundle はありません（最初の承認で作られます）'}
            </p>
            <Button asChild size="sm" className="ml-auto bg-brand text-white">
              <Link to={paths.knowledgeNew(slug, kind)}>{label}を作る</Link>
            </Button>
          </div>
          {rows.length === 0 ? (
            <Panel className="text-sm text-ink-2">まだ{label}はありません。</Panel>
          ) : (
            <ul className="space-y-2" aria-label={`${label}の一覧`}>
              {rows.map((a) => (
                <li key={a.artifact_id} className="row flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
                  <Link to={paths.knowledgeItem(slug, kind, a.artifact_id)} className="min-w-0 flex-1 truncate font-mono font-medium text-primary underline-offset-4 hover:underline">
                    {a.name}
                  </Link>
                  {a.active_revision !== null ? (
                    <ToneBadge tone="ok">有効 改訂 {a.active_revision}</ToneBadge>
                  ) : (
                    <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">未承認</span>
                  )}
                  {a.draft_revision !== null && <ToneBadge tone="rev">下書き 改訂 {a.draft_revision}</ToneBadge>}
                  <Link to={paths.knowledgeRevisions(slug, kind, a.artifact_id)} className="text-xs text-primary underline-offset-4 hover:underline">
                    改訂と承認
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
