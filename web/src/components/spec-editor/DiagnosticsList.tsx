import { ToneBadge } from '@/components/app/ToneBadge'
import { sortDiagnostics } from '@/services/specPreview'
import type { DiagnosticsListProps } from '@/types/specEditor'

/** SpecLino の parser の診断（場所つき）。押すと本文のその行へ移る。 */
export function DiagnosticsList({ diagnostics, onJump }: DiagnosticsListProps) {
  if (diagnostics.length === 0) {
    return <p className="text-sm text-ink-3">診断はありません。</p>
  }
  return (
    <ul className="space-y-2" aria-label="診断">
      {sortDiagnostics(diagnostics).map((d, i) => {
        const span = d.span
        const where = span ? `${span.line} 行 ${span.column} 列` : '場所なし'
        const body = (
          <>
            <span className="flex flex-wrap items-center gap-2">
              <ToneBadge tone={d.severity === 'error' ? 'bad' : 'unk'}>{d.severity === 'error' ? 'エラー' : '警告'}</ToneBadge>
              <code className="font-mono text-xs">{d.code}</code>
              <span className="text-xs text-ink-3">
                {where}
                {span && span.diagram_id !== '(document)' ? ` · 図 ${span.diagram_id}` : ''}
              </span>
            </span>
            <span className="mt-1 block text-sm break-words">{d.message}</span>
          </>
        )
        return (
          <li key={`${d.code}-${i}`}>
            {span ? (
              <button
                type="button"
                className="row block w-full px-3 py-2 text-left hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                onClick={() => onJump(span.line, span.column)}
                aria-label={`${where}へ移る: ${d.message}`}
              >
                {body}
              </button>
            ) : (
              <div className="row px-3 py-2">{body}</div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
