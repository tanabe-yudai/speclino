import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { getProjectOverview } from '@/api/projects'
import { getRun } from '@/api/reviewRuns'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { NextActionsCard } from '@/components/project/NextActionsCard'
import { PoliciesPanel } from '@/components/project/PoliciesPanel'
import { RequirementsPanel } from '@/components/project/RequirementsPanel'
import { RunHistory } from '@/components/project/RunHistory'
import { StatusPanel } from '@/components/project/StatusPanel'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { nextActions } from '@/services/nextActions'
import { paths } from '@/services/paths'
import type { Finding } from '@/types/finding'
import type { ProjectOverview, ProjectSummary } from '@/types/project'

/** プロジェクト画面（S45、design 5 章）。URL のリポジトリを一覧から引き、中身は project ごとに作り直す。 */
export function Project() {
  const { slug, owner, repo } = useParams<{ slug: string; owner: string; repo: string }>()
  const fullName = `${owner}/${repo}`
  const { projects, error: listError, retry: retryList } = useWorkspaceProjects(slug)
  const project = projects?.find((p) => p.repo_full_name === fullName) ?? null

  if (listError) return <ErrorState message={listError} onRetry={retryList} />
  if (projects === null) return <LoadingState />
  if (!project || !slug) {
    return (
      <Panel>
        <PanelTitle className="break-all">このワークスペースに {fullName} はありません</PanelTitle>
      </Panel>
    )
  }
  // 同じルートのままプロジェクトを切り替えても前の状態・反例を持ち越さない（key で作り直す）
  return <ProjectBody key={`${slug}/${project.id}`} slug={slug} project={project} />
}

function ProjectBody({ slug, project }: { slug: string; project: ProjectSummary }) {
  const fullName = project.repo_full_name
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [findings, setFindings] = useState<Finding[] | null>(null)
  const [findingsRequested, setFindingsRequested] = useState(false)

  useEffect(() => {
    let alive = true
    getProjectOverview(slug, project.id)
      .then((o) => alive && setOverview(o))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError ? err.message : 'プロジェクトの状態を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, project.id, attempt])

  const runId = overview?.run?.id
  // 反例などの本文は、要求の行を初めて開いたときに検査の詳細から 1 回だけ取る
  const loadFindings = useCallback(() => {
    if (!runId || findingsRequested) return
    setFindingsRequested(true)
    getRun(runId)
      .then((r) => setFindings(r.findings))
      .catch(() => setFindings([]))
  }, [runId, findingsRequested])

  function retry() {
    setError(null)
    setOverview(null)
    setAttempt((n) => n + 1)
  }

  const [, repo] = fullName.split('/')
  const header = (
    <header className="min-w-0">
      <p className="font-mono text-xs break-all text-ink-3">{fullName}</p>
      <h1 className="mt-1 text-2xl font-bold tracking-tight break-all">{repo}</h1>
    </header>
  )

  if (error) {
    return (
      <div className="space-y-6">
        {header}
        <ErrorState message={error} onRetry={retry} />
      </div>
    )
  }
  if (!overview) return <LoadingState message="プロジェクトの状態を取得しています…" />

  if (overview.status !== 'ok' || !overview.run) {
    return (
      <div className="space-y-6">
        {header}
        <Panel>
          <PanelTitle>
            {overview.status === 'not_checked' ? 'まだ検査されていません' : '最新の検査の成果物を読めませんでした'}
          </PanelTitle>
          {overview.status === 'not_checked' ? (
            <p className="mt-1 text-sm text-ink-2">
              PR を開くと最初の検査が始まります。{' '}
              <Link className="text-primary underline-offset-4 hover:underline" to={paths.settings(slug, 'projects')}>
                はじめ方を見る
              </Link>
            </p>
          ) : (
            overview.run && (
              <Link className="mt-2 inline-block text-sm text-primary hover:underline" to={paths.run(overview.run.id)}>
                検査の詳細
              </Link>
            )
          )}
        </Panel>
        <RunHistory runs={overview.runs} />
      </div>
    )
  }

  const run = overview.run
  return (
    <div className="space-y-6">
      {header}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 lg:col-start-1">
          <StatusPanel overview={overview} />
        </div>
        <aside className="min-w-0 self-start lg:sticky lg:top-20 lg:col-start-2 lg:row-span-4 lg:row-start-1">
          <NextActionsCard actions={nextActions(overview.requirements, overview.policies)} run={run} />
        </aside>
        <div className="min-w-0 lg:col-start-1">
          <RequirementsPanel
            requirements={overview.requirements}
            unextracted={overview.unextracted}
            run={run}
            repo={fullName}
            findings={findings}
            onOpen={loadFindings}
          />
        </div>
        <div className="min-w-0 lg:col-start-1">
          <PoliciesPanel overview={overview} />
        </div>
        <div className="min-w-0 lg:col-start-1">
          <RunHistory runs={overview.runs} />
        </div>
      </div>
    </div>
  )
}
