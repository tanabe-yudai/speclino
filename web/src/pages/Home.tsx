import { useEffect, useState, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { getObservability } from '@/api/observability'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ProjectCard } from '@/components/app/ProjectCard'
import { RunRow } from '@/components/app/RunRow'
import { ErrorState } from '@/components/ErrorState'
import { KnowledgeStateBadges } from '@/components/knowledge/KnowledgeStateBadges'
import { LoadingState } from '@/components/LoadingState'
import { useArtifacts } from '@/hooks/useArtifacts'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { useMe } from '@/hooks/useMe'
import { usePolicySummary } from '@/hooks/usePolicySummary'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { useWorkspaceRuns } from '@/hooks/useWorkspaceRuns'
import { cn } from '@/lib/utils'
import { staleRepositories, type StaleRepository } from '@/services/home'
import { KNOWLEDGE_KIND_LABELS, SYSTEM_RULES } from '@/services/labels'
import { legacyRedirect } from '@/services/legacyRoutes'
import { KNOWLEDGE_KINDS, artifactStates, knowledgeCounts, pendingArtifacts, policyStates } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import { failingPullRequests } from '@/services/runList'
import { pickWorkspace } from '@/services/workspaces'
import type { WorkspaceRole } from '@/types/me'

const TODO_LIMIT = 5

/** ワークスペースのホーム（`/w/:slug`）: やること・知識の件数・プロジェクトの健全性・最近の検査。 */
export function Home() {
  const { me } = useMe()
  const { slug: urlSlug } = useParams<{ slug?: string }>()
  const { pathname, search } = useLocation()
  const workspace = pickWorkspace(me?.workspaces ?? [], urlSlug)
  useDocumentTitle(workspace ? (workspace.name ?? workspace.slug) : undefined)

  // 旧いタブ（?tab=shared など）はスキル・規約・観測の画面へ
  const legacy = urlSlug ? legacyRedirect(pathname, search) : null
  if (legacy) return <Navigate to={legacy} replace />
  if (!me) return <LoadingState />

  if (me.workspaces.length === 0) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">所属しているワークスペースがありません</PanelTitle>
        <p className="mt-2 text-sm text-ink-2">
          招待リンクを受け取っている場合は、そのリンクを開いてください。招待が無い場合は、管理者にこのメールアドレスを伝えて招待してもらってください。
        </p>
        <code className="mt-3 inline-block rounded-md bg-muted px-3 py-1.5 font-mono text-xs select-all">{me.email ?? 'メールアドレスなし'}</code>
      </Panel>
    )
  }

  // URL の slug が所属に無い: 別のワークスペースの内容に化けさせない
  if (!workspace) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">このワークスペースは見つかりません</PanelTitle>
        <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
          ホームへ戻る
        </Link>
      </Panel>
    )
  }

  return (
    <div className="space-y-8">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Workspace</p>
        <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{workspace.name ?? workspace.slug}</h1>
      </header>
      <HomeBody key={workspace.slug} slug={workspace.slug} role={workspace.role} />
    </div>
  )
}

function HomeBody({ slug, role }: { slug: string; role: WorkspaceRole }) {
  const { artifacts } = useArtifacts(slug)
  const policy = usePolicySummary(slug)
  const { runs, error: runsError, retry: retryRuns } = useWorkspaceRuns(slug, 50)
  const { projects, error: projectsError, retry: retryProjects } = useWorkspaceProjects(slug)
  const [stale, setStale] = useState<StaleRepository[] | null>(null)

  // 知識の鮮度は観測の集計から。取れなければその節を出さない（やることの補助）
  useEffect(() => {
    let alive = true
    getObservability(slug)
      .then((h) => alive && setStale(staleRepositories(h)))
      .catch(() => alive && setStale([]))
    return () => {
      alive = false
    }
  }, [slug])

  const canApprove = role === 'owner' || role === 'approver'
  const pending = artifacts ? pendingArtifacts(artifacts) : null
  const policyPending = policy.row?.draftRevision != null
  const pendingCount = (pending?.length ?? 0) + (policyPending ? 1 : 0)
  const failing = runs ? failingPullRequests(runs) : null
  const counts = artifacts ? knowledgeCounts(artifacts, policy.row?.rules ?? (policy.loading ? null : 0)) : null
  const loadingTodo = pending === null || failing === null || stale === null
  const nothingToDo = !loadingTodo && pendingCount === 0 && failing.length === 0 && stale.length === 0

  return (
    <>
      <section className="space-y-3" aria-labelledby="todo-heading">
        <h2 id="todo-heading" className="text-sm font-semibold text-ink-2">
          やること
        </h2>
        {loadingTodo && <LoadingState message="やることを集めています…" />}
        {nothingToDo && <Panel className="text-sm text-ink-2">今やることはありません。</Panel>}
        {pending !== null && pendingCount > 0 && (
          <TodoPanel
            title={canApprove ? `承認待ち ${pendingCount} 件（あなたが承認できます）` : `承認待ち ${pendingCount} 件`}
            emphasis={canApprove}
          >
            <ul className="space-y-1.5">
              {policyPending && policy.row && (
                <li className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <span className="w-14 text-xs text-ink-3">{KNOWLEDGE_KIND_LABELS.policy}</span>
                  <Link className="min-w-0 flex-1 truncate text-primary underline-offset-4 hover:underline" to={paths.policies(slug)}>
                    チームの規約
                  </Link>
                  <KnowledgeStateBadges states={policyStates(policy.row).filter((s) => s === 'pending')} revisions={{ pending: policy.row.draftRevision }} />
                </li>
              )}
              {pending.map((a) => (
                <li key={a.artifact_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <span className="w-14 text-xs text-ink-3">{KNOWLEDGE_KIND_LABELS[a.kind]}</span>
                  <Link
                    className="min-w-0 flex-1 truncate font-mono text-primary underline-offset-4 hover:underline"
                    to={paths.knowledgeItem(slug, a.kind, a.artifact_id)}
                  >
                    {a.name}
                  </Link>
                  <KnowledgeStateBadges states={artifactStates(a)} revisions={{ active: a.active_revision, retired: a.retired_revision, pending: a.draft_revision }} />
                </li>
              ))}
            </ul>
          </TodoPanel>
        )}
        {failing !== null && failing.length > 0 && (
          <TodoPanel
            title={`ゲートが失敗の PR ${failing.length} 件（違反・要確認）`}
            more={{ to: paths.runs(slug, { gate: 'fail' }), label: '検査の一覧で見る' }}
          >
            <div className="space-y-2">
              {failing.slice(0, TODO_LIMIT).map((run) => (
                <RunRow key={run.id} slug={slug} run={run} />
              ))}
            </div>
          </TodoPanel>
        )}
        {stale !== null && stale.length > 0 && (
          <TodoPanel
            title={`${SYSTEM_RULES['KNOWLEDGE-LOCK-001'].title}（${stale.length} 件）`}
            more={{ to: paths.settings(slug, 'agents'), label: 'リポジトリ同期の手順' }}
          >
            <p className="text-xs text-ink-3">{SYSTEM_RULES['KNOWLEDGE-LOCK-001'].hint}（ゲートでは KNOWLEDGE-LOCK-001）。</p>
            <ul className="mt-2 space-y-1 text-sm">
              {stale.map((s) => (
                <li key={s.repo} className="flex flex-wrap gap-x-3">
                  <span className="min-w-0 flex-1 truncate font-mono">{s.repo}</span>
                  <span className="text-xs text-ink-2">{s.behindBy} 世代遅れ</span>
                </li>
              ))}
            </ul>
          </TodoPanel>
        )}
        {runsError && <ErrorState message={runsError} onRetry={retryRuns} />}
      </section>

      <section className="space-y-3" aria-labelledby="knowledge-heading">
        <h2 id="knowledge-heading" className="text-sm font-semibold text-ink-2">
          知識
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {KNOWLEDGE_KINDS.map((k) => (
            <Link
              key={k}
              to={paths.knowledgeList(slug, k)}
              className="row flex flex-col gap-1 p-4"
            >
              <span className="text-xs text-ink-3">{KNOWLEDGE_KIND_LABELS[k]}</span>
              <span className="text-2xl font-bold tabular-nums">{counts?.[k] ?? '—'}</span>
              {k === 'policy' && <span className="text-xs text-ink-3">ルールの数</span>}
            </Link>
          ))}
        </div>
      </section>

      <section className="space-y-3" aria-labelledby="projects-heading">
        <h2 id="projects-heading" className="text-sm font-semibold text-ink-2">
          プロジェクトの健全性
        </h2>
        {projectsError && <ErrorState message={projectsError} onRetry={retryProjects} />}
        {!projectsError && projects === null && <LoadingState message="プロジェクトを取得しています…" />}
        {projects !== null && projects.length === 0 && (
          <Panel className="text-sm text-ink-2">
            まだリポジトリが登録されていません。
            {role === 'owner' && (
              <>
                {' '}
                <Link className="text-primary underline-offset-4 hover:underline" to={paths.settings(slug, 'repositories')}>
                  リポジトリを追加する
                </Link>
              </>
            )}
          </Panel>
        )}
        {projects !== null && projects.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,24rem),1fr))] gap-4">
            {projects.map((p) => (
              <ProjectCard key={p.id} slug={slug} project={p} />
            ))}
          </div>
        )}
      </section>

      {runs !== null && runs.length > 0 && (
        <section className="space-y-3" aria-labelledby="runs-heading">
          <div className="flex items-center gap-3">
            <h2 id="runs-heading" className="text-sm font-semibold text-ink-2">
              最近の検査
            </h2>
            <Link className="ml-auto text-sm text-primary underline-offset-4 hover:underline" to={paths.runs(slug)}>
              すべての検査
            </Link>
          </div>
          <div className="space-y-2">
            {runs.slice(0, TODO_LIMIT).map((run) => (
              <RunRow key={run.id} slug={slug} run={run} />
            ))}
          </div>
        </section>
      )}
    </>
  )
}

/** やることの 1 つの塊。emphasis は承認できる人への承認待ち（枠を強める）。 */
function TodoPanel({
  title,
  emphasis = false,
  more,
  children,
}: {
  title: string
  emphasis?: boolean
  more?: { to: string; label: string }
  children: ReactNode
}) {
  return (
    <Panel className={cn('space-y-3', emphasis && 'border-primary/40 ring-1 ring-primary/30')}>
      <div className="flex flex-wrap items-center gap-3">
        <PanelTitle>{title}</PanelTitle>
        {more && (
          <Link className="ml-auto text-sm text-primary underline-offset-4 hover:underline" to={more.to}>
            {more.label}
          </Link>
        )}
      </div>
      {children}
    </Panel>
  )
}
