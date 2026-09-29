import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getMe } from '../api/me'
import { listRuns } from '../api/reviewRuns'
import { ApiError } from '../api/errors'
import { Badge } from '../components/Badge'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { formatDateTime, shortSha } from '../services/format'
import { EXEC_STATE_LABELS, GATE_STATUS_LABELS, GATE_TONE } from '../services/labels'
import type { Me } from '../types/me'
import type { ReviewRunSummary } from '../types/run'

function targetLabel(run: ReviewRunSummary): string {
  const repo = run.repo_full_name ?? '(repo 不明)'
  return run.pr_number != null ? `${repo}#${run.pr_number}` : repo
}

export function RunList() {
  const [runs, setRuns] = useState<ReviewRunSummary[] | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const fetchRuns = useCallback(() => {
    // run が見えるのは所属ワークスペースに登録されたリポジトリだけなので、空のときに理由を示すため
    // 所属も一緒に取る
    Promise.all([listRuns(50), getMe()])
      .then(([fetchedRuns, fetchedMe]) => {
        setRuns(fetchedRuns)
        setMe(fetchedMe)
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : 'run 一覧の取得に失敗しました')
      })
  }, [])

  useEffect(() => {
    fetchRuns()
  }, [fetchRuns])

  function retry() {
    setRuns(null)
    setError(null)
    fetchRuns()
  }

  return (
    <main className="run-list-page">
      <h1>SpecLino レビュー実行一覧</h1>
      <p className="muted">新しい順。行をクリックすると詳細（判定・反例・タイムライン）を確認できます。</p>

      {runs === null && error === null && <LoadingState message="run 一覧を取得しています…" />}
      {error && <ErrorState message={error} onRetry={retry} />}

      {runs !== null &&
        runs.length === 0 &&
        (me?.workspaces.length === 0 ? (
          <p className="muted">
            所属しているワークスペースがありません。管理者にこの uid を伝えて追加してもらってください:{' '}
            <code>{me.uid}</code>
          </p>
        ) : (
          <p className="muted">まだ run がありません。</p>
        ))}

      {runs !== null && runs.length > 0 && (
        <table className="run-table">
          <thead>
            <tr>
              <th>対象</th>
              <th>head</th>
              <th>ゲート</th>
              <th>実行状態</th>
              <th>作成時刻</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                className="run-row"
                onClick={() => navigate(`/runs/${run.id}`)}
              >
                <td>
                  <Link to={`/runs/${run.id}`} onClick={(e) => e.stopPropagation()}>
                    {targetLabel(run)}
                  </Link>
                </td>
                <td>
                  <code>{shortSha(run.head_sha)}</code>
                </td>
                <td>
                  {run.gate_status ? (
                    <Badge tone={GATE_TONE[run.gate_status]}>{GATE_STATUS_LABELS[run.gate_status]}</Badge>
                  ) : (
                    <Badge tone="neutral">未確定</Badge>
                  )}
                </td>
                <td>{EXEC_STATE_LABELS[run.exec_state]}</td>
                <td>{formatDateTime(run.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}
