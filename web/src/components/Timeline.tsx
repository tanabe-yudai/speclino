import { cn } from '@/lib/utils'
import { formatDateTime } from '../services/format'
import { eventMeta } from '../services/labels'
import type { TimelineProps } from '../types/components'

const DOT: Record<ReturnType<typeof eventMeta>['group'], string> = {
  state: 'bg-primary',
  agent: 'bg-unk',
  tool: 'bg-ink-3',
  audit: 'bg-rev',
  other: 'bg-muted-foreground/50',
}

export function Timeline({ events }: TimelineProps) {
  if (events.length === 0) {
    return <p className="text-sm text-ink-3">イベントの記録がありません</p>
  }
  const sorted = [...events].sort((a, b) => a.seq - b.seq)
  return (
    <ol className="relative space-y-4 border-l border-border pl-5">
      {sorted.map((event) => {
        const meta = eventMeta(event.type)
        const hasPayload = Object.keys(event.payload).length > 0
        return (
          <li key={event.seq} className="relative">
            <span
              aria-hidden
              className={cn('absolute top-1.5 -left-[1.6rem] size-2.5 rounded-full ring-4 ring-background', DOT[meta.group])}
            />
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm">
              <span className="font-medium">{meta.label}</span>
              <span className="font-mono text-xs text-ink-3">{event.type}</span>
              <time className="text-xs text-ink-3" dateTime={event.ts}>
                {formatDateTime(event.ts)}
              </time>
            </div>
            {hasPayload && (
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-ink-3">詳細</summary>
                <pre className="mt-1 overflow-x-auto rounded-md bg-muted/70 p-3 font-mono">
                  {JSON.stringify(event.payload, null, 2)}
                </pre>
              </details>
            )}
          </li>
        )
      })}
    </ol>
  )
}
