import { cn } from '@/lib/utils'
import { healthPercent, ringSegments, STATE_META, statusRatio } from '@/services/health'
import type { StatusCounts } from '@/types/project'

/**
 * プロジェクトの状態の円グラフ（要求と規約を合わせた 5 区分。色は状態の見出し・件数のタイルと同じ）。
 * 中央は全体のうちの適合の割合。counts が null か 0 件なら「—」。
 */
export function HealthRing({ counts, size = 'sm' }: { counts: StatusCounts | null; size?: 'sm' | 'lg' }) {
  const segments = ringSegments(counts)
  const ratio = statusRatio(counts)
  const pct = ratio === null ? null : healthPercent(ratio)
  const outer = size === 'lg' ? 'size-24' : 'size-12'
  const inner = size === 'lg' ? 'size-18 text-xl' : 'size-9 text-xs'
  const background =
    segments.length === 0
      ? 'var(--muted)'
      : `conic-gradient(${segments.map((s) => `var(--${STATE_META[s.kind].tone}) ${s.from}% ${s.to}%`).join(', ')})`
  const label =
    counts === null || pct === null
      ? '判定なし'
      : `${segments.map((s) => `${STATE_META[s.kind].segment} ${counts[s.kind]}`).join('・')}（適合 ${pct}%）`
  return (
    <div className={cn('grid shrink-0 place-items-center rounded-full', outer)} style={{ background }} role="img" aria-label={label}>
      <span className={cn('grid place-items-center rounded-full bg-card font-bold', inner)}>
        {pct === null ? (
          '—'
        ) : (
          <span>
            {pct}
            <span className="text-[0.6em] font-semibold">%</span>
          </span>
        )}
      </span>
    </div>
  )
}
