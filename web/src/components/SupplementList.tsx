import { formatDateTime } from '../services/format'
import type { SupplementListProps } from '../types/components'

export const SUPPLEMENT_LABEL = 'エージェントからの補足（正式な回答ではありません）'

/** REVIEW の所見に添える補足。判定の色は使わず、判定・ゲートには影響しないことが見た目で分かるようにする。 */
export function SupplementList({ supplements }: SupplementListProps) {
  if (supplements.length === 0) return null
  return (
    <div className="space-y-2 rounded-md border border-dashed bg-muted/50 px-3 py-2.5">
      <p className="text-xs font-semibold text-ink-3">{SUPPLEMENT_LABEL}</p>
      <ul className="space-y-2">
        {supplements.map((s) => (
          <li key={s.id} className="space-y-0.5">
            <p className="text-sm whitespace-pre-wrap text-ink-2 [overflow-wrap:anywhere]">{s.content}</p>
            <p className="text-xs text-ink-3">
              {s.source} ・ {formatDateTime(s.created_at)}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}
