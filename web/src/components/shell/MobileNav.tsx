import { Menu } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { cn } from '@/lib/utils'
import { isCurrentEntry, mobileNavEntries } from '@/services/mobileNav'

/** スマホ幅（sm 未満）のメニュー。上部バーの左のハンバーガーから左のシートを開く。 */
export function MobileNav() {
  const workspace = useCurrentWorkspace()
  const { owner, repo } = useParams<{ owner?: string; repo?: string }>()
  const { pathname, search } = useLocation()
  const [open, setOpen] = useState(false)
  const slug = workspace?.slug
  const projects = useWorkspaceProjects(slug).projects ?? []
  if (!slug) return null

  const entries = mobileNavEntries({ slug, projects, currentRepo: owner && repo ? `${owner}/${repo}` : null })
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" className="sm:hidden" aria-label="メニューを開く">
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent aria-describedby="mobile-nav-desc">
        <SheetTitle>{workspace.name ?? workspace.slug}</SheetTitle>
        <SheetDescription id="mobile-nav-desc" className="sr-only">
          ワークスペースの画面を選びます
        </SheetDescription>
        <nav aria-label="メニュー" className="mt-2 flex flex-col gap-0.5">
          {entries.map((e) => {
            const current = isCurrentEntry(e.to, pathname, search)
            return (
              <SheetClose key={e.to} asChild>
                <Link
                  to={e.to}
                  aria-current={current ? 'page' : undefined}
                  className={cn(
                    'truncate rounded-md px-3 py-2 text-ink-2 hover:bg-muted hover:text-foreground',
                    e.depth === 1 && 'ml-4 py-1.5 text-[13px]',
                    e.project && 'font-medium',
                    current && 'bg-muted font-semibold text-foreground',
                  )}
                >
                  {e.label}
                </Link>
              </SheetClose>
            )
          })}
        </nav>
      </SheetContent>
    </Sheet>
  )
}
