import { Panel, PanelTitle } from '@/components/app/Panel'
import { formatDateTime } from '@/services/format'
import { REVISION_STATUS_LABELS } from '@/services/labels'
import type { ActorLabelFn } from '@/types/member'
import type { RevisionSummary } from '@/types/policy'

/** 改訂の履歴（新しい順）。 */
export function RevisionHistory({ items, actorLabel }: { items: RevisionSummary[]; actorLabel: ActorLabelFn }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>改訂の履歴</PanelTitle>
      {items.length === 0 ? (
        <p className="text-sm text-ink-3">改訂はまだありません。</p>
      ) : (
        <ol className="space-y-3">
          {items.map((r) => (
            <li key={r.revision} className="min-w-0 text-sm">
              <p className="flex flex-wrap items-center gap-x-2">
                <span className="font-semibold">改訂 {r.revision}</span>
                <span className="text-xs text-ink-2">{REVISION_STATUS_LABELS[r.status]}</span>
                <span className="text-xs text-ink-3">ルール {r.rule_count}</span>
              </p>
              <p className="text-xs break-all text-ink-3">
                {formatDateTime(r.approved_at ?? r.created_at)} · 作成 {actorLabel(r.author)}
                {r.approver && ` · 承認 ${actorLabel(r.approver)}`}
              </p>
              {r.note && <p className="text-xs break-words text-ink-2">{r.note}</p>}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
