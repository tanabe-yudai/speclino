import { RotateCw } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { rerunRun } from '../api/reviewRuns'
import { ApiError } from '../api/errors'
import type { RerunButtonProps } from '../types/components'
import type { RerunResponse } from '../types/run'

/** 検査の再実行（S39）。回数上限・費用枠の判断はサーバー側で行い、ここは結果を表示する。 */
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
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="secondary" onClick={handleClick} disabled={pending}>
        <RotateCw className="size-4" />
        {pending ? '再実行を開始しています…' : '再実行'}
      </Button>
      {error && <span className="text-sm text-bad">{error}</span>}
    </div>
  )
}
