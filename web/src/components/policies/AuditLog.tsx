import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { formatDateTime } from '@/services/format'
import { auditLabel } from '@/services/labels'
import type { AuditEntry } from '@/types/policy'

/** 監査ログ（誰が・いつ・何を。承認の拒否も含む）。 */
export function AuditLog({ items }: { items: AuditEntry[] }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>監査ログ</PanelTitle>
      {items.length === 0 ? (
        <p className="text-sm text-ink-3">記録はまだありません。</p>
      ) : (
        <ol className="space-y-2.5">
          {items.map((a, i) => {
            const label = auditLabel(a.action, a.decision)
            return (
              <li key={`${a.ts}-${i}`} className="min-w-0 text-sm">
                <p className="flex flex-wrap items-center gap-x-2">
                  {a.decision === 'denied' ? <ToneBadge tone="bad">{label}</ToneBadge> : <span>{label}</span>}
                  <span className="text-xs text-ink-3">改訂 {a.target.split('@').pop()}</span>
                </p>
                <p className="text-xs break-all text-ink-3">
                  {formatDateTime(a.ts)} · {a.actor}
                  {a.detail.self_approved === true && ' · 作成者本人の承認'}
                </p>
              </li>
            )
          })}
        </ol>
      )}
    </Panel>
  )
}
