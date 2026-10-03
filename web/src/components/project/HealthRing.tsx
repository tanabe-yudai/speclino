import { cn } from '@/lib/utils'
import { healthPercent, healthRatio, ringSegments, type RingKind } from '@/services/health'
import type { VerdictCounts } from '@/types/project'

const COLOR: Record<RingKind, string> = {
  ok: 'var(--ok)',
  rev: 'var(--rev)',
  unk: 'var(--unk)',
  bad: 'var(--bad)',
  none: 'var(--muted)',
}

/**
 * 要求の判定の内訳の円グラフ（適合・要確認・未検証・違反・未解析）。中央は適合率。
 * counts が null か 0 件なら「—」。
 */
export function HealthRing({
  counts,
  unanalyzed = 0,
  size = 'sm',
}: {
  counts: VerdictCounts | null
  unanalyzed?: number
  size?: 'sm' | 'lg'
}) {
  const segments = ringSegments(counts, unanalyzed)
  const ratio = healthRatio(counts, unanalyzed)
  const pct = ratio === null ? null : healthPercent(ratio)
  const outer = size === 'lg' ? 'size-24' : 'size-12'
  const inner = size === 'lg' ? 'size-18 text-xl' : 'size-9 text-xs'
  const background =
    segments.length === 0
      ? 'var(--muted)'
      : `conic-gradient(${segments.map((s) => `${COLOR[s.kind]} ${s.from}% ${s.to}%`).join(', ')})`
  const label =
    counts === null || pct === null
      ? '判定なし'
      : `要求の判定: 適合 ${counts.VERIFIED_IN_SCOPE}・要確認 ${counts.REVIEW}・未検証 ${counts.UNKNOWN}・違反 ${counts.VIOLATED}・未解析 ${unanalyzed}（適合率 ${pct}%）`
  return (
    <div className={cn('grid shrink-0 place-items-center rounded-full', outer)} style={{ background }} role="img" aria-label={label}>
      <span className={cn('grid place-items-center rounded-full bg-card font-bold', inner)}>{pct === null ? '—' : pct}</span>
    </div>
  )
}
