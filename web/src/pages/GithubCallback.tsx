import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { completeRegistration } from '../api/repositories'
import { ApiError } from '../api/errors'
import { LoadingState } from '../components/LoadingState'
import type { RegistrationResult } from '../types/repository'

/** GitHub の認可から戻ってきたところ。code と state をサーバーに渡して登録を終える（S55）。 */
export function GithubCallback() {
  const [params] = useSearchParams()
  const [result, setResult] = useState<RegistrationResult | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  // StrictMode で effect が 2 回走っても 1 回だけ送る（state は 1 回しか使えない）
  const sent = useRef(false)
  const code = params.get('code')
  const state = params.get('state')
  const denied = params.get('error') !== null || !code || !state
  const error = denied
    ? 'GitHub での認可が完了しませんでした。リポジトリの画面からやり直してください。'
    : apiError

  useEffect(() => {
    if (sent.current || denied || !code || !state) return
    sent.current = true
    completeRegistration(code, state)
      .then(setResult)
      .catch((err: unknown) => {
        setApiError(err instanceof ApiError ? err.message : '登録に失敗しました')
      })
  }, [code, state, denied])

  return (
    <main className="run-list-page">
      <h1>リポジトリの登録</h1>
      {result === null && error === null && <LoadingState message="GitHub で確認しています…" />}
      {error && (
        <>
          <p className="form-error">{error}</p>
          <p className="muted">
            SpecLino の GitHub App がリポジトリにインストールされているか、あなたがそのリポジトリの
            admin かを確かめてください。
          </p>
          <p>
            <Link to="/">一覧へ戻る</Link>
          </p>
        </>
      )}
      {result && (
        <>
          <p>
            {result.repo_full_name} を{result.status === 'registered' ? '登録しました' : 'すでに登録済みです'}。
          </p>
          <p>
            <Link to={`/workspaces/${result.slug}/repositories`}>リポジトリの画面へ</Link>
          </p>
        </>
      )}
    </main>
  )
}
