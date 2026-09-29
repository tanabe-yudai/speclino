import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getMe } from '../api/me'
import { listRepositories, startRegistration } from '../api/repositories'
import { ApiError } from '../api/errors'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { githubRepoUrl, isRepoFullName } from '../services/repositories'
import type { RegisteredRepository } from '../types/repository'

/** ワークスペースに登録されたリポジトリの一覧と、owner による追加（S55）。 */
export function Repositories() {
  const { slug } = useParams<{ slug: string }>()
  if (!slug) {
    return <ErrorState message="ワークスペースが指定されていません" />
  }
  return <RepositoriesView key={slug} slug={slug} />
}

function RepositoriesView({ slug }: { slug: string }) {
  const [repos, setRepos] = useState<RegisteredRepository[] | null>(null)
  const [isOwner, setIsOwner] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [repoName, setRepoName] = useState('')
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const fetchAll = useCallback(() => {
    Promise.all([listRepositories(slug), getMe()])
      .then(([fetched, me]) => {
        setRepos(fetched)
        setIsOwner(me.workspaces.some((w) => w.slug === slug && w.role === 'owner'))
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'リポジトリ一覧の取得に失敗しました')
      })
  }, [slug])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  function handleAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const name = repoName.trim()
    if (!isRepoFullName(name)) {
      setStartError('owner/name の形で入力してください（例: your-org/your-repo）')
      return
    }
    setStarting(true)
    setStartError(null)
    startRegistration(slug, name)
      .then(({ authorize_url }) => window.location.assign(authorize_url))
      .catch((err: unknown) => {
        setStartError(err instanceof ApiError ? err.message : '登録を始められませんでした')
        setStarting(false)
      })
  }

  return (
    <main className="run-list-page">
      <p>
        <Link to="/">← 一覧へ</Link>
      </p>
      <h1>リポジトリ（{slug}）</h1>
      <p className="muted">
        登録したリポジトリの PR だけが、このワークスペースのメンバーに表示されます。
      </p>

      {repos === null && error === null && <LoadingState message="リポジトリを取得しています…" />}
      {error && <ErrorState message={error} onRetry={fetchAll} />}

      {repos !== null && repos.length === 0 && <p className="muted">まだ登録されていません。</p>}
      {repos !== null && repos.length > 0 && (
        <ul className="repository-list">
          {repos.map((r) => (
            <li key={r.repo_full_name}>
              <a href={githubRepoUrl(r.repo_full_name)} target="_blank" rel="noreferrer">
                {r.repo_full_name}
              </a>{' '}
              <span className="muted">（既定ブランチ {r.default_branch}）</span>
            </li>
          ))}
        </ul>
      )}

      {isOwner && (
        <form className="inline-form" onSubmit={handleAdd}>
          <label>
            追加するリポジトリ
            <input
              value={repoName}
              onChange={(e) => setRepoName(e.target.value)}
              placeholder="owner/name"
              autoComplete="off"
            />
          </label>
          <button type="submit" className="button-primary" disabled={starting}>
            GitHub で確認して追加
          </button>
          <p className="muted">
            GitHub で本人確認し、あなたがそのリポジトリの admin で、SpecLino の GitHub App
            がインストールされていることを確かめてから登録します。
          </p>
          {startError && <p className="form-error">{startError}</p>}
        </form>
      )}
    </main>
  )
}
