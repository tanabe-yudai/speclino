import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listRuns } from '@/api/reviewRuns'
import { ApiError } from '@/api/errors'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { RunRow } from '@/components/app/RunRow'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { HealthRing } from '@/components/project/HealthRing'
import { useMe } from '@/hooks/useMe'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { STATE_META, TILE_ORDER } from '@/services/health'
import { paths } from '@/services/paths'
import { pickWorkspace } from '@/services/workspaces'
import type { ProjectSummary } from '@/types/project'
import type { ReviewRunSummary } from '@/types/run'

/** 1 つのプロジェクトのカード（健康状態・違反などの件数・最新のゲート）。 */
function ProjectCard({ slug, project }: { slug: string; project: ProjectSummary }) {
  const [, name] = project.repo_full_name.split('/')
  return (
    <Link to={paths.project(slug, project.repo_full_name)} className="row flex min-w-0 items-center gap-4 p-5">
      <HealthRing counts={project.status_counts} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold">{name}</p>
        <p className="mt-0.5 truncate font-mono text-xs text-ink-3">{project.repo_full_name}</p>
        {project.status === 'unavailable' ? (
          <p className="mt-2 text-xs text-ink-3">最新の検査の成果物を読めません</p>
        ) : project.state && project.status_counts ? (
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-2">
            <ToneBadge tone={STATE_META[project.state].tone}>{STATE_META[project.state].label}</ToneBadge>
            {TILE_ORDER.filter((k) => k !== 'ok' && (project.status_counts?.[k] ?? 0) > 0).map((k) => (
              <span key={k}>
                {STATE_META[k].segment} {project.status_counts?.[k]}
              </span>
            ))}
          </p>
        ) : (
          <p className="mt-2 text-xs text-ink-3">まだ検査されていません</p>
        )}
      </div>
      {project.latest_run?.gate_status && <GateBadge status={project.latest_run.gate_status} />}
    </Link>
  )
}

/** ワークスペースのホーム（`/w/:slug`）: プロジェクトのカードと、そのワークスペースの最近の検査。 */
export function Home() {
  const { me } = useMe()
  const { slug: urlSlug } = useParams<{ slug?: string }>()
  const workspace = pickWorkspace(me?.workspaces ?? [], urlSlug)
  const slug = workspace?.slug
  const { projects, error: projectsError, retry: retryProjects } = useWorkspaceProjects(slug)
  const [runs, setRuns] = useState<ReviewRunSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchRuns = useCallback(() => {
    listRuns(50)
      .then(setRuns)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : '検査の一覧を取得できませんでした'))
  }, [])

  useEffect(() => {
    fetchRuns()
  }, [fetchRuns])

  function retry() {
    setError(null)
    setRuns(null)
    fetchRuns()
  }

  if (!me) return <LoadingState />

  if (me.workspaces.length === 0) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">所属しているワークスペースがありません</PanelTitle>
        <p className="mt-2 text-sm text-ink-2">管理者にこの uid を伝えて、ワークスペースに追加してもらってください。</p>
        <code className="mt-3 inline-block rounded-md bg-muted px-3 py-1.5 font-mono text-xs select-all">{me.uid}</code>
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

  const names = projects ? new Set(projects.map((p) => p.repo_full_name)) : null
  const recent = runs && names ? runs.filter((r) => names.has(r.repo_full_name ?? '')) : null

  return (
    <div className="space-y-8">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Workspace</p>
        <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{workspace.name ?? workspace.slug}</h1>
      </header>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-2">プロジェクト</h2>
        {projectsError && <ErrorState message={projectsError} onRetry={retryProjects} />}
        {!projectsError && projects === null && <LoadingState message="プロジェクトを取得しています…" />}
        {projects !== null && projects.length === 0 && (
          <Panel className="text-sm text-ink-2">
            まだリポジトリが登録されていません。
            {workspace.role === 'owner' && (
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard key={p.id} slug={slug} project={p} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-2">最近の検査</h2>
        {error && <ErrorState message={error} onRetry={retry} />}
        {!error && recent === null && !projectsError && <LoadingState message="検査の一覧を取得しています…" />}
        {recent !== null && recent.length === 0 && <p className="text-sm text-ink-3">まだ検査がありません。</p>}
        {recent !== null && recent.length > 0 && (
          <div className="space-y-2">
            {recent.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
