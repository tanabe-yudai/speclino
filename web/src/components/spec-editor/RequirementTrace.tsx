import { ToneBadge } from '@/components/app/ToneBadge'
import { cn } from '@/lib/utils'
import type { RequirementTraceProps } from '@/types/specEditor'

const LEVEL_LABELS = { mandatory: '必須', advisory: '助言' } as const

/** 要求の表の追跡状況。形式化の無い行は「未形式化」。必須の未形式化はゲートで REVIEW のまま残る。 */
export function RequirementTrace({ trace, hasCompiled }: RequirementTraceProps) {
  if (!hasCompiled) {
    return <p className="text-sm text-ink-3">診断のエラーを直すと、要求の表の追跡状況が出ます。</p>
  }
  if (trace.total === 0) {
    return (
      <p className="text-sm text-ink-3">
        要求の表（<code className="font-mono text-xs">| ID | level | 要求 | 形式化 |</code>）がありません。
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-2">
        形式化 {trace.formalized} / {trace.total} 件
        {trace.gateReview > 0 && (
          <span className="text-rev">（必須の未形式化 {trace.gateReview} 件はゲートで REVIEW になります）</span>
        )}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-ink-3">
            <tr>
              <th scope="col" className="py-1 pr-3">ID</th>
              <th scope="col" className="py-1 pr-3">level</th>
              <th scope="col" className="py-1 pr-3">要求</th>
              <th scope="col" className="py-1">形式化</th>
            </tr>
          </thead>
          <tbody>
            {trace.rows.map((r) => (
              <tr key={r.id} className={cn('border-t align-top', !r.formalized && 'bg-unk-bg/40')}>
                <td className="py-1.5 pr-3 font-mono text-xs">{r.id}</td>
                <td className="py-1.5 pr-3 whitespace-nowrap">{LEVEL_LABELS[r.level]}</td>
                <td className="py-1.5 pr-3 break-words">{r.text}</td>
                <td className="py-1.5">
                  {r.formalized ? (
                    <span className="font-mono text-xs break-all">{r.formalized_by.join(', ')}</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">
                      <ToneBadge tone="unk">未形式化</ToneBadge>
                      {r.gateReview && <ToneBadge tone="rev">ゲートで REVIEW</ToneBadge>}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
