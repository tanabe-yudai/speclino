import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { VerdictTone } from '@/types/components'
import { TONE_CLASS } from './toneClass'

/** 判定の色つきのバッジ。色の前に点を付け、色だけに頼らない。 */
export function ToneBadge({
  tone,
  children,
  className,
}: {
  tone: VerdictTone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap',
        TONE_CLASS[tone],
        className,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  )
}
