import { GATE_STATUS_LABELS, gateTone } from '@/services/labels'
import type { GateStatus } from '@/types/run'
import { ToneBadge } from './ToneBadge'

/** ゲートの結果のバッジ。まだ確定していなければ「未確定」。 */
export function GateBadge({ status }: { status: GateStatus | null }) {
  if (!status) {
    return <span className="inline-flex rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">未確定</span>
  }
  return <ToneBadge tone={gateTone(status)}>{GATE_STATUS_LABELS[status]}</ToneBadge>
}
