import { formatDateTime } from '../services/format'
import { eventMeta } from '../services/labels'
import type { TimelineProps } from '../types/components'

export function Timeline({ events }: TimelineProps) {
  if (events.length === 0) {
    return <p className="muted">イベントの記録がありません</p>
  }
  const sorted = [...events].sort((a, b) => a.seq - b.seq)
  return (
    <ol className="timeline">
      {sorted.map((event) => {
        const meta = eventMeta(event.type)
        const hasPayload = Object.keys(event.payload).length > 0
        return (
          <li key={event.seq} className={`timeline-item timeline-group-${meta.group}`}>
            <span className="timeline-dot" aria-hidden="true" />
            <div className="timeline-body">
              <div className="timeline-heading">
                <span className="timeline-label">{meta.label}</span>
                <span className="timeline-type">{event.type}</span>
                <time className="timeline-ts" dateTime={event.ts}>
                  {formatDateTime(event.ts)}
                </time>
              </div>
              {hasPayload && (
                <details className="timeline-payload">
                  <summary>詳細</summary>
                  <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                </details>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
