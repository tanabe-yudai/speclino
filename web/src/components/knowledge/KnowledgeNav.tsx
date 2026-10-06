import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { KIND_LABELS } from '@/services/knowledge'
import { paths } from '@/services/paths'
import type { ArtifactKind } from '@/types/knowledge'
import type { KnowledgeNavProps } from '@/types/specEditor'

const KINDS: ArtifactKind[] = ['spec', 'skill', 'instructions']
const linkClass = (active: boolean) =>
  cn(
    'rounded-md px-3 py-1.5 text-sm text-ink-2 transition-colors hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
    active && 'bg-background font-semibold text-foreground shadow-sm',
  )

/** 知識の 4 種類の切り替え。規約は既存の画面へ移る。 */
export function KnowledgeNav({ slug, current }: KnowledgeNavProps) {
  return (
    <nav aria-label="知識の種類" className="-mx-1 overflow-x-auto px-1">
      <div className="inline-flex gap-1 rounded-lg bg-muted p-1">
        {KINDS.map((k) => (
          <NavLink key={k} to={paths.knowledge(slug, k)} className={() => linkClass(k === current)} aria-current={k === current ? 'page' : undefined}>
            {KIND_LABELS[k]}
          </NavLink>
        ))}
        <NavLink to={paths.policies(slug)} className={() => linkClass(false)}>
          規約
        </NavLink>
      </div>
    </nav>
  )
}
