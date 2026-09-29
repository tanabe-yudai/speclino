import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { completeRegistration } from '../api/repositories'
import { ApiError } from '../api/errors'
import { LoadingState } from '../components/LoadingState'
import { Panel } from '@/components/app/Panel'
import { paths } from '@/services/paths'
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
    <div className="mx-auto max-w-xl">
      <Panel className="space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">リポジトリの登録</h1>
        {result === null && error === null && <LoadingState message="GitHub で確認しています…" />}
        {error && (
          <>
            <p className="text-sm text-bad" role="alert">
              {error}
            </p>
            <p className="text-sm text-ink-2">
              SpecLino の GitHub App がリポジトリにインストールされているか、あなたがそのリポジトリの admin
              かを確かめてください。
            </p>
            <Link className="text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
              ホームへ戻る
            </Link>
          </>
        )}
        {result && (
          <>
            <p className="flex items-center gap-2 text-sm">
              <span aria-hidden className="size-2 rounded-full bg-ok" />
              <span className="font-mono">{result.repo_full_name}</span>
              を{result.status === 'registered' ? '登録しました' : 'すでに登録済みです'}。
            </p>
            <Link
              className="text-sm text-primary underline-offset-4 hover:underline"
              to={paths.settings(result.slug, 'repositories')}
            >
              リポジトリの設定へ
            </Link>
          </>
        )}
      </Panel>
    </div>
  )
}
