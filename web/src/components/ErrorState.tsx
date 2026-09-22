import type { ErrorStateProps } from '../types/components'

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="state-panel state-error" role="alert">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="button-secondary">
          再試行
        </button>
      )}
    </div>
  )
}
