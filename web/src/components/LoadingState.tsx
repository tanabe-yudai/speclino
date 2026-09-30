import { Skeleton } from '@/components/ui/skeleton'
import type { LoadingStateProps } from '../types/components'

/** 読み込み中。文言は読み上げ用に残し、見た目は骨組みの影で出す。 */
export function LoadingState({ message = '読み込み中…' }: LoadingStateProps) {
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <span className="sr-only">{message}</span>
      <Skeleton className="h-14 w-full rounded-md" />
      <Skeleton className="h-14 w-full rounded-md" />
      <Skeleton className="h-14 w-4/5 rounded-md" />
    </div>
  )
}
