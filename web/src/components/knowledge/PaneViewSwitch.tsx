import { cn } from '@/lib/utils'
import { PANE_VIEWS, paneViewLabel } from '@/services/paneView'
import type { PaneViewSwitchProps } from '@/types/paneView'

/** lg 未満で本文とプレビューを切り替える。ヘッダー（h-14）の下に貼り付き、長い本文でも届く。 */
export function PaneViewSwitch({ view, onChange, diagnosticCount }: PaneViewSwitchProps) {
  return (
    <div className="sticky top-14 z-30 -mx-4 border-b bg-background/90 px-4 py-2 backdrop-blur-md sm:-mx-6 sm:px-6 lg:hidden">
      <div role="group" aria-label="表示の切り替え" className="inline-flex rounded-lg border p-0.5">
        {PANE_VIEWS.map((v) => (
          <button
            key={v.value}
            type="button"
            aria-pressed={view === v.value}
            onClick={() => onChange(v.value)}
            className={cn('rounded-md px-3 py-1 text-sm', view === v.value ? 'bg-muted font-medium' : 'text-ink-3')}
          >
            {paneViewLabel(v.value, diagnosticCount)}
          </button>
        ))}
      </div>
    </div>
  )
}
