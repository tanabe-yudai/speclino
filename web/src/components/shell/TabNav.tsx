import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { TabItem } from '@/types/navigation'

/** URL で切り替えるタブ（リンクの並び）。スマホ幅では横にスクロールする。 */
export function TabNav({ label, items, current }: { label: string; items: TabItem[]; current: string }) {
  return (
    <nav aria-label={label} className="-mx-1 overflow-x-auto px-1">
      <div className="inline-flex gap-1 rounded-lg bg-muted p-1">
        {items.map((t) => (
          <Link
            key={t.id}
            to={t.to}
            replace
            aria-current={t.id === current ? 'page' : undefined}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm whitespace-nowrap text-ink-2 transition-colors hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
              t.id === current && 'bg-background font-semibold text-foreground shadow-sm',
            )}
          >
            {t.label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
