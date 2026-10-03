/**
 * プロジェクトの状態（要求と規約を合わせた 5 区分）。円グラフ・状態の見出し・件数のタイルは
 * すべてバックエンドの `status_counts` 1 つから作り、同じ名前と色を使う（同期させる）。
 */

import type { VerdictTone } from '@/types/components'
import type { ProjectState, StatusCounts } from '@/types/project'

export const STATE_META: Record<ProjectState, { label: string; segment: string; tone: VerdictTone }> = {
  violated: { label: '違反あり', segment: '違反', tone: 'bad' },
  review: { label: '要確認あり', segment: '要確認', tone: 'rev' },
  unknown: { label: '未検証あり', segment: '未検証', tone: 'unk' },
  exempted: { label: '免除中あり', segment: '免除中', tone: 'exempt' },
  ok: { label: '健全', segment: '適合', tone: 'ok' },
}

/** 円の並び（適合から始めて、重いものほど後ろ）。 */
export const RING_ORDER: ProjectState[] = ['ok', 'exempted', 'unknown', 'review', 'violated']

/** 件数のタイルの並び（重い順。状態の見出しと同じ順）。 */
export const TILE_ORDER: ProjectState[] = ['violated', 'review', 'unknown', 'exempted', 'ok']

function total(c: StatusCounts): number {
  return c.violated + c.review + c.unknown + c.exempted + c.ok
}

/** 全体のうち適合の割合（0〜1）。判定が 1 件も無ければ null（0% と言わない）。 */
export function statusRatio(c: StatusCounts | null): number | null {
  if (!c) return null
  const t = total(c)
  return t === 0 ? null : c.ok / t
}

/** 表示用の百分率。切り捨てる（問題が 1 件でもあれば 100 と出さない）。 */
export function healthPercent(ratio: number): number {
  return Math.floor(ratio * 100)
}

export interface RingSegment {
  kind: ProjectState
  /** 円の中の開始位置と終了位置（%） */
  from: number
  to: number
}

export function ringSegments(c: StatusCounts | null): RingSegment[] {
  if (!c) return []
  const t = total(c)
  if (t === 0) return []
  const out: RingSegment[] = []
  let at = 0
  for (const kind of RING_ORDER) {
    const v = c[kind]
    if (v === 0) continue
    const to = at + (v / t) * 100
    out.push({ kind, from: Math.round(at * 100) / 100, to: Math.round(to * 100) / 100 })
    at = to
  }
  return out
}
