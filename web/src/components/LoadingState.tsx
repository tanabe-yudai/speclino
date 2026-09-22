import type { LoadingStateProps } from '../types/components'

export function LoadingState({ message = '読み込み中…' }: LoadingStateProps) {
  return (
    <div className="state-panel state-loading" role="status">
      {message}
    </div>
  )
}
