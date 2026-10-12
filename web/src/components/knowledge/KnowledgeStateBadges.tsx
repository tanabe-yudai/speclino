import { ToneBadge } from '@/components/app/ToneBadge'
import { KNOWLEDGE_STATE_LABELS } from '@/services/labels'
import { KNOWLEDGE_STATE_TONE } from '@/services/knowledgeNav'
import type { KnowledgeState } from '@/types/navigation'

/** 知識の状態のバッジ（一覧・ホームで共通）。状態ごとの改訂番号があれば添える。 */
export function KnowledgeStateBadges({
  states,
  revisions,
}: {
  states: KnowledgeState[]
  revisions: Partial<Record<KnowledgeState, number | null>>
}) {
  return (
    <>
      {states.map((s) => {
        const rev = revisions[s]
        const text = rev ? `${KNOWLEDGE_STATE_LABELS[s]} 改訂 ${rev}` : KNOWLEDGE_STATE_LABELS[s]
        const tone = KNOWLEDGE_STATE_TONE[s]
        return tone ? (
          <ToneBadge key={s} tone={tone}>
            {text}
          </ToneBadge>
        ) : (
          <span key={s} className="rounded-full bg-muted px-2.5 py-1 text-xs whitespace-nowrap text-ink-3">
            {text}
          </span>
        )
      })}
    </>
  )
}
