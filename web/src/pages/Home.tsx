import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listRepositories } from '@/api/repositories'
import { listRuns } from '@/api/reviewRuns'
import { ApiError } from '@/api/errors'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { RunRow } from '@/components/app/RunRow'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import type { RegisteredRepository } from '@/types/repository'
import type { ReviewRunSummary } from '@/types/run'

/** ホーム: 今のワークスペースのプロジェクトと、最近の検査。 */
export function Home() {
  const { me } = useMe()
  const workspace = useCurrentWorkspace()
  const [repos, setRepos] = useState<RegisteredRepository[] | null>(null)
  const [runs, setRuns] = useState<ReviewRunSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reposError, setReposError] = useState<string | null>(null)
  const [reposAttempt, setReposAttempt] = useState(0)
  const slug = workspace?.slug

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

  useEffect(() => {
    if (!slug) return
    let alive = true
    listRepositories(slug)
      .then((r) => alive && setRepos(r))
      .catch((err: unknown) => {
        if (alive) setReposError(err instanceof ApiError ? err.message : 'プロジェクトの一覧を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, reposAttempt])

  function retryRepos() {
    setReposError(null)
    setRepos(null)
    setReposAttempt((n) => n + 1)
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

  return (
    <div className="space-y-8">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Workspace</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">{workspace?.name ?? workspace?.slug ?? 'ホーム'}</h1>
      </header>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-2">プロジェクト</h2>
        {repos === null && reposError === null && slug && <LoadingState message="プロジェクトを取得しています…" />}
        {reposError && <ErrorState message={reposError} onRetry={retryRepos} />}
        {repos !== null && repos.length === 0 && (
          <Panel className="text-sm text-ink-2">
            まだリポジトリが登録されていません。
            {workspace?.role === 'owner' && slug && (
              <>
                {' '}
                <Link className="text-primary underline-offset-4 hover:underline" to={paths.settings(slug, 'repositories')}>
                  リポジトリを追加する
                </Link>
              </>
            )}
          </Panel>
        )}
        {repos !== null && repos.length > 0 && slug && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {repos.map((r) => (
              <Link
                key={r.repo_full_name}
                to={paths.project(slug, r.repo_full_name)}
                className="row block min-w-0 p-5"
              >
                <p className="truncate font-semibold">{r.repo_full_name.split('/')[1]}</p>
                <p className="mt-1 truncate font-mono text-xs text-ink-3">{r.repo_full_name}</p>
                <p className="mt-3 text-xs text-ink-2">既定ブランチ {r.default_branch}</p>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-2">最近の検査</h2>
        {runs === null && error === null && <LoadingState message="検査の一覧を取得しています…" />}
        {error && <ErrorState message={error} onRetry={retry} />}
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
