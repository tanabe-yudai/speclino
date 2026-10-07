import { useCallback, useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { listRuns } from '@/api/reviewRuns'
import { ApiError } from '@/api/errors'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ProjectCard } from '@/components/app/ProjectCard'
import { RunRow } from '@/components/app/RunRow'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { ArtifactSection } from '@/components/knowledge/ArtifactSection'
import { TabNav } from '@/components/shell/TabNav'
import { useArtifacts } from '@/hooks/useArtifacts'
import { useMe } from '@/hooks/useMe'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { sharedArtifactRows } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import { WORKSPACE_TABS, WORKSPACE_TAB_LABELS, pickWorkspaceTab } from '@/services/workspaceTabs'
import { pickWorkspace } from '@/services/workspaces'
import { PoliciesBody } from './Policies'
import { ObservabilityBody } from './observability/Observability'
import type { ReviewRunSummary } from '@/types/run'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** ワークスペースのホーム（`/w/:slug`）: プロジェクト / チーム共通 / チーム規約 / 利用状況のタブ（`?tab=`）。 */
export function Home() {
  const { me } = useMe()
  const { slug: urlSlug } = useParams<{ slug?: string }>()
  const [params] = useSearchParams()
  const workspace = pickWorkspace(me?.workspaces ?? [], urlSlug)
  const slug = workspace?.slug
  const tab = pickWorkspaceTab(params.get('tab'))
  useDocumentTitle(workspace ? (workspace.name ?? workspace.slug) : undefined)

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
  if (!workspace || !slug) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">このワークスペースは見つかりません</PanelTitle>
        <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
          ホームへ戻る
        </Link>
      </Panel>
    )
  }

  const name = workspace.name ?? workspace.slug
  return (
    <div className="space-y-6">
      <header className="space-y-4">
        <div>
          <p className="text-xs font-semibold tracking-widest text-primary uppercase">Workspace</p>
          <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{name}</h1>
        </div>
        <TabNav
          label="ワークスペースの画面"
          current={tab}
          items={WORKSPACE_TABS.map((t) => ({ id: t, label: WORKSPACE_TAB_LABELS[t], to: paths.workspace(slug, t) }))}
        />
      </header>
      {tab === 'projects' && <ProjectsTab key={slug} slug={slug} canAddRepo={workspace.role === 'owner'} />}
      {tab === 'shared' && <SharedTab key={slug} slug={slug} />}
      {tab === 'policies' && <PoliciesBody key={slug} slug={slug} name={name} embedded />}
      {tab === 'usage' && <ObservabilityBody key={slug} slug={slug} name={name} embedded />}
    </div>
  )
}

/** プロジェクトのカードと、そのワークスペースの最近の検査。 */
function ProjectsTab({ slug, canAddRepo }: { slug: string; canAddRepo: boolean }) {
  const { projects, error: projectsError, retry: retryProjects } = useWorkspaceProjects(slug)
  const [runs, setRuns] = useState<ReviewRunSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  // 最近の検査は API 側でこのワークスペースに絞る。タブは slug ごとに作り直すので前の一覧は残らない
  useEffect(() => {
    let alive = true
    listRuns(50, slug)
      .then((r) => alive && setRuns(r))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError ? err.message : '検査の一覧を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    setError(null)
    setRuns(null)
    setAttempt((n) => n + 1)
  }, [])

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        {projectsError && <ErrorState message={projectsError} onRetry={retryProjects} />}
        {!projectsError && projects === null && <LoadingState message="プロジェクトを取得しています…" />}
        {projects !== null && projects.length === 0 && (
          <Panel className="text-sm text-ink-2">
            まだリポジトリが登録されていません。
            {canAddRepo && (
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
        {projects !== null && projects.some((p) => p.status === 'not_checked') && (
          <p className="text-sm text-ink-3">
            まだ検査されていないプロジェクトがあります。{' '}
            <Link className="text-primary underline-offset-4 hover:underline" to={paths.settings(slug, 'projects')}>
              はじめ方を見る
            </Link>
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-2">最近の検査</h2>
        {error && <ErrorState message={error} onRetry={retry} />}
        {!error && runs === null && <LoadingState message="検査の一覧を取得しています…" />}
        {runs !== null && runs.length === 0 && <p className="text-sm text-ink-3">まだ検査がありません。</p>}
        {runs !== null && runs.length > 0 && (
          <div className="space-y-2">
            {runs.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

/** チーム共通: ワークスペース全体に効くスキルと指示書。作る操作の初期スコープはワークスペース全体。 */
function SharedTab({ slug }: { slug: string }) {
  const { artifacts, error, retry } = useArtifacts(slug)
  if (error) return <ErrorState message={error} onRetry={retry} />
  return (
    <div className="space-y-8">
      <ArtifactSection
        slug={slug}
        kind="skill"
        heading="スキル"
        rows={artifacts ? sharedArtifactRows(artifacts, 'skill') : null}
        error={null}
        onRetry={retry}
      />
      <ArtifactSection
        slug={slug}
        kind="instructions"
        heading="指示書"
        rows={artifacts ? sharedArtifactRows(artifacts, 'instructions') : null}
        error={null}
        onRetry={retry}
      />
    </div>
  )
}
