import { ChevronRight } from 'lucide-react'
import { Fragment } from 'react'
import { Link } from 'react-router-dom'
import type { Crumb } from '@/types/navigation'

/** パンくず（services/breadcrumbs.ts の段）。最後の段は今の画面。 */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="パンくずリスト" className="min-w-0">
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-sm text-ink-3">
        {items.map((c, i) => (
          <Fragment key={`${i}:${c.label}`}>
            {i > 0 && <ChevronRight aria-hidden className="size-3.5 shrink-0" />}
            <li className="min-w-0 truncate">
              {c.to ? (
                <Link to={c.to} className="text-primary underline-offset-4 hover:underline">
                  {c.label}
                </Link>
              ) : (
                <span aria-current="page" className="text-ink-2">
                  {c.label}
                </span>
              )}
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  )
}
