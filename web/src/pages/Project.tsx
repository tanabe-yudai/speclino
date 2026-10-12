import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { getProjectOverview } from '@/api/projects'
import { getRun } from '@/api/reviewRuns'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { KnowledgeBrowser } from '@/components/knowledge/KnowledgeBrowser'
import { Breadcrumbs } from '@/components/shell/Breadcrumbs'
import { TabNav } from '@/components/shell/TabNav'
import { NextActionsCard } from '@/components/project/NextActionsCard'
import { GettingStarted } from '@/components/project/GettingStarted'
import { PoliciesPanel } from '@/components/project/PoliciesPanel'
import { RequirementsPanel } from '@/components/project/RequirementsPanel'
import { StatusPanel } from '@/components/project/StatusPanel'
import { RunBrowser } from '@/components/runs/RunBrowser'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { PROJECT_TAB_LABELS, SECTION_LABELS } from '@/services/labels'
import { legacyRedirect } from '@/services/legacyRoutes'
import { nextActions } from '@/services/nextActions'
import { paths } from '@/services/paths'
import { PROJECT_TABS, parseProjectTab } from '@/services/projectTabs'
import type { Finding } from '@/types/finding'
import type { ProjectTab } from '@/types/navigation'
import type { ProjectOverview, ProjectSummary } from '@/types/project'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/**
 * プロジェクト画面（`/w/:slug/p/:owner/:repo/(overview|knowledge|runs)`）。知識と検査のタブは、
 * 第 1 階層の一覧をこのプロジェクトで絞ったもの（同じ部品）。URL のリポジトリを一覧から引き、中身は project ごとに作り直す。
 */
export function Project() {
  const { slug, owner, repo, tab: rawTab } = useParams<{ slug: string; owner: string; repo: string; tab?: string }>()
  const { pathname, search } = useLocation()
  const fullName = `${owner}/${repo}`
  useDocumentTitle(repo)
  const { projects, error: listError, retry: retryList } = useWorkspaceProjects(slug)
  const project = projects?.find((p) => p.repo_full_name === fullName) ?? null
  const tab = parseProjectTab(rawTab)
  // 旧いタブ（仕様・スキル・指示書・規約）は知識のタブへ、知らないタブは概要へ
  if (tab === null && slug) return <Navigate to={legacyRedirect(pathname, search) ?? paths.project(slug, fullName)} replace />

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
  return <ProjectBody key={`${slug}/${project.id}`} slug={slug} project={project} tab={tab ?? 'overview'} />
}

function ProjectBody({ slug, project, tab }: { slug: string; project: ProjectSummary; tab: ProjectTab }) {
  const fullName = project.repo_full_name
  const [, repo] = fullName.split('/')
  return (
    <div className="space-y-6">
      <header className="min-w-0 space-y-3">
        <Breadcrumbs items={[{ label: SECTION_LABELS.home, to: paths.workspace(slug) }, { label: repo }]} />
        <div>
          <p className="font-mono text-xs break-all text-ink-3">{fullName}</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight break-all">{repo}</h1>
        </div>
        <TabNav
          label="プロジェクトの画面"
          current={tab}
          items={PROJECT_TABS.map((t) => ({ id: t, label: PROJECT_TAB_LABELS[t], to: paths.project(slug, fullName, t) }))}
        />
      </header>
      {tab === 'knowledge' && <KnowledgeBrowser slug={slug} project={project} />}
      {tab === 'runs' && <RunBrowser slug={slug} project={project} />}
      {tab === 'overview' && <Overview slug={slug} project={project} />}
    </div>
  )
}

/** 概要（最新の検査の結果から作る）。まだ検査が無い・読めないときは理由を出す。 */
function Overview({ slug, project }: { slug: string; project: ProjectSummary }) {
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

  if (error) return <ErrorState message={error} onRetry={retry} />
  if (!overview) return <LoadingState message="プロジェクトの状態を取得しています…" />
  const run = overview.run
  if (overview.status !== 'ok' || !run) {
    return (
      <div className="space-y-4">
        <Panel>
          <PanelTitle>
            {overview.status === 'not_checked' ? 'まだ検査されていません' : '最新の検査の成果物を読めませんでした'}
          </PanelTitle>
          {overview.status === 'not_checked' ? (
            <p className="mt-1 text-sm text-ink-2">PR を開くと最初の検査が始まります。</p>
          ) : (
            overview.run && (
              <Link className="mt-2 inline-block text-sm text-primary hover:underline" to={paths.run(slug, overview.run.id)}>
                検査の詳細
              </Link>
            )
          )}
        </Panel>
        {overview.status === 'not_checked' && <GettingStarted slug={slug} fullName={fullName} />}
      </div>
    )
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0 lg:col-start-1">
        <StatusPanel slug={slug} overview={overview} />
      </div>
      <aside className="min-w-0 self-start lg:sticky lg:top-20 lg:col-start-2 lg:row-span-3 lg:row-start-1">
        <NextActionsCard slug={slug} actions={nextActions(overview.requirements, overview.policies)} run={run} />
      </aside>
      <div className="min-w-0 lg:col-start-1">
        <RequirementsPanel
          slug={slug}
          requirements={overview.requirements}
          unextracted={overview.unextracted}
          run={run}
          repo={fullName}
          findings={findings}
          onOpen={loadFindings}
        />
      </div>
      <div className="min-w-0 space-y-2 lg:col-start-1">
        <PoliciesPanel overview={overview} />
        <p className="text-sm">
          <Link className="text-primary underline-offset-4 hover:underline" to={paths.policies(slug)}>
            規約を編集
          </Link>
        </p>
      </div>
    </div>
  )
}
