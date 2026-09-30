import { cn } from '@/lib/utils'
import { healthPercent } from '@/services/health'

/** 健康状態のリング（適合の割合）。ratio が null なら判定なし。 */
export function HealthRing({ ratio, size = 'sm' }: { ratio: number | null; size?: 'sm' | 'lg' }) {
  const pct = ratio === null ? 0 : healthPercent(ratio)
  const outer = size === 'lg' ? 'size-24' : 'size-12'
  const inner = size === 'lg' ? 'size-18 text-xl' : 'size-9 text-xs'
  return (
    <div
      className={cn('grid shrink-0 place-items-center rounded-full', outer)}
      style={{ background: ratio === null ? 'var(--muted)' : `conic-gradient(var(--ok) 0 ${pct}%, var(--muted) ${pct}% 100%)` }}
      role="img"
      aria-label={ratio === null ? '判定なし' : `適合 ${pct}%`}
    >
      <span className={cn('grid place-items-center rounded-full bg-card font-bold', inner)}>
        {ratio === null ? '—' : pct}
      </span>
    </div>
  )
}
