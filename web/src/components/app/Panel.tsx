import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

/** 光の空間のパネル（半透明＋背景のぼかし＋柔らかい影）。 */
export function Panel({ className, ...props }: ComponentProps<'section'>) {
  return (
    <section
      className={cn(
        'rounded-lg border border-white/70 bg-surface p-5 shadow-soft backdrop-blur-md dark:border-white/5',
        className,
      )}
      {...props}
    />
  )
}

export function PanelTitle({ className, ...props }: ComponentProps<'h2'>) {
  return <h2 className={cn('text-sm font-semibold text-foreground', className)} {...props} />
}
