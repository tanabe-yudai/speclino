import { useState } from 'react'
import { rerunRun } from '../api/reviewRuns'
import { ApiError } from '../api/errors'
import type { RerunButtonProps } from '../types/components'
import type { RerunResponse } from '../types/run'

/** デモ再実行ボタン（S39）。回数上限・費用枠の判断はサーバー側で行い、ここは結果を表示する。 */
export function RerunButton({ runId, onStarted }: RerunButtonProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClick() {
    setPending(true)
    setError(null)
    rerunRun(runId)
      .then((res: RerunResponse) => onStarted(res))
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : '再実行の開始に失敗しました')
      })
      .finally(() => setPending(false))
  }

  return (
    <div className="rerun-block">
      <button type="button" className="button-primary" onClick={handleClick} disabled={pending}>
        {pending ? '再実行を開始しています…' : 'デモを再実行'}
      </button>
      {error && <span className="rerun-error">{error}</span>}
    </div>
  )
}
