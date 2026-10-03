import type { VerdictTone } from '@/types/components'

/** 判定の色ごとの文字色と地の色（globals.css のトークン）。 */
export const TONE_CLASS: Record<VerdictTone, string> = {
  ok: 'text-ok bg-ok-bg',
  bad: 'text-bad bg-bad-bg',
  unk: 'text-unk bg-unk-bg',
  rev: 'text-rev bg-rev-bg',
  exempt: 'text-exempt bg-exempt-bg',
}
