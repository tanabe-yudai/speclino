import { Activity, FileText, House, ListChecks, Ellipsis } from 'lucide-react'
import type { ComponentType } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useMe } from '@/hooks/useMe'
import { cn } from '@/lib/utils'
import { MORE_LABEL, SECTION_LABELS, WORKSPACE_ROLE_LABELS } from '@/services/labels'
import { BOTTOM_TABS, MORE_SECTIONS, currentSection, sectionPath } from '@/services/navSections'
import { paths } from '@/services/paths'
import type { SectionId } from '@/types/navigation'

const ICONS: Partial<Record<SectionId, ComponentType<{ className?: string }>>> = {
  home: House,
  specs: FileText,
  runs: ListChecks,
  observability: Activity,
}

const TAB_CLASS =
  'flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] text-ink-3 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50'

/**
 * スマホ幅（sm 未満）の画面下に固定するタブ（ホーム・仕様・検査・観測・その他）。
 * その他はスキル・指示書・規約・設定・ワークスペースの切り替え。高さは h-14 ＋ 下端の安全領域（下端に固定する保存の帯はこの分だけ上げる）。
 */
export function BottomNav() {
  const workspace = useCurrentWorkspace()
  const { me } = useMe()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  if (!workspace) return null
  const slug = workspace.slug
  const section = currentSection(pathname)
  const moreActive = section !== null && MORE_SECTIONS.includes(section)
  return (
    <nav
      aria-label="主な画面"
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden"
    >
      <div className="flex h-14">
        {BOTTOM_TABS.map((id) => {
          const Icon = ICONS[id] ?? House
          return (
            <Link
              key={id}
              to={sectionPath(slug, id)}
              aria-current={section === id ? 'page' : undefined}
              className={cn(TAB_CLASS, section === id && 'font-semibold text-foreground')}
            >
              <Icon className="size-5" />
              {SECTION_LABELS[id]}
            </Link>
          )
        })}
        <DropdownMenu>
          <DropdownMenuTrigger className={cn(TAB_CLASS, moreActive && 'font-semibold text-foreground')}>
            <Ellipsis className="size-5" />
            {MORE_LABEL}
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="end" className="w-60">
            {MORE_SECTIONS.map((id) => (
              <DropdownMenuItem key={id} onSelect={() => navigate(sectionPath(slug, id))}>
                <span className={cn(section === id && 'font-semibold')}>{SECTION_LABELS[id]}</span>
              </DropdownMenuItem>
            ))}
            {me && me.workspaces.length > 1 && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>ワークスペースを切り替える</DropdownMenuLabel>
                {me.workspaces.map((w) => (
                  <DropdownMenuItem key={w.slug} onSelect={() => navigate(paths.workspace(w.slug))}>
                    <span className={cn('truncate', w.slug === slug && 'font-semibold')}>{w.name ?? w.slug}</span>
                    <span className="ml-auto text-xs text-ink-3">{WORKSPACE_ROLE_LABELS[w.role]}</span>
                  </DropdownMenuItem>
                ))}
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  )
}
