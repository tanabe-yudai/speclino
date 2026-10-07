import { Panel } from '@/components/app/Panel'
import type { ScopeBlockProps } from '../types/components'

function Row({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="grid gap-1 sm:grid-cols-[7rem_1fr]">
      <span className="text-xs font-semibold text-ink-3">{label}</span>
      <ul className="list-disc space-y-0.5 pl-4 text-sm text-ink-2 sm:pl-0 sm:list-none">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 検査の仮定・未確認範囲と条件。「何を確認し、何を確認していないか」は判定の確かさに関わるので常に見せ、
 * 内部の条件（深さ・上限など）だけを畳む。
 */
export function ScopeBlock({ title, scopeItems, assumptions, unresolvedItems }: ScopeBlockProps) {
  const hasAny = scopeItems.length > 0 || assumptions.length > 0 || unresolvedItems.length > 0
  return (
    <Panel>
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mt-3 space-y-3">
        {!hasAny && <p className="text-sm text-ink-3">記録なし</p>}
        <Row label="仮定" items={assumptions} />
        <Row label="未確認範囲" items={unresolvedItems} />
        {scopeItems.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs font-semibold text-ink-3">検査の条件</summary>
            <div className="mt-2">
              <Row label="条件" items={scopeItems} />
            </div>
          </details>
        )}
      </div>
    </Panel>
  )
}
