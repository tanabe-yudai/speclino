import type { CounterexamplePanelProps } from '../types/components'

const KIND_LABELS: Record<'symbolic' | 'observed', string> = {
  symbolic: '記号的（静的解析による反例）',
  observed: '観測（テスト実行による反例）',
}

export function CounterexamplePanel({ counterexample }: CounterexamplePanelProps) {
  const assignments = Object.entries(counterexample.assignments).sort(([a], [b]) => a.localeCompare(b))
  return (
    <div className="rounded-md bg-muted/70 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
        <span className="font-semibold text-bad">反例</span>
        <span>{KIND_LABELS[counterexample.kind]}</span>
        {counterexample.path_id && <span className="font-mono">経路: {counterexample.path_id}</span>}
      </div>
      <p className="mt-2 text-ink-2">{counterexample.description}</p>
      {assignments.length > 0 && (
        <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 font-mono text-xs">
          {assignments.map(([name, value]) => (
            <div key={name} className="contents">
              <dt className="text-ink-3 break-all">{name}</dt>
              <dd className="min-w-0 text-foreground break-all">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
