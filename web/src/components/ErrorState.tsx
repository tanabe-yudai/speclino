import { Button } from '@/components/ui/button'
import { Panel } from '@/components/app/Panel'
import type { ErrorStateProps } from '../types/components'

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <Panel role="alert" className="flex flex-wrap items-center gap-3">
      <span className="text-bad">{message}</span>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="ml-auto">
          再試行
        </Button>
      )}
    </Panel>
  )
}
