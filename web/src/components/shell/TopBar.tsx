import { Link, useLocation } from 'react-router-dom'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { cn } from '@/lib/utils'
import { SECTION_LABELS } from '@/services/labels'
import { SECTIONS, currentSection, sectionPath } from '@/services/navSections'
import { paths } from '@/services/paths'
import { AccountMenu } from './AccountMenu'
import { ProjectPill } from './ProjectPill'
import { ThemeToggle } from './ThemeToggle'
import { WorkspacePill } from './WorkspacePill'
import { BrandMark } from './BrandMark'

/**
 * ログイン後の上部に固定するバー（design 3.3）。PC 幅ではワークスペースの第 1 階層
 * （ホーム・仕様・スキル・指示書・規約・検査・観測・設定）を並べ、スマホ幅ではワークスペース名とアカウントだけ（画面の行き来は下のタブ）。
 */
export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const workspace = useCurrentWorkspace()
  const { pathname } = useLocation()
  const section = currentSection(pathname)
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <Link to={paths.home()} className="mr-1 shrink-0 font-extrabold tracking-tight">
          <BrandMark />
          <span className="hidden lg:inline"> SpecLino</span>
        </Link>
        <WorkspacePill />
        <ProjectPill />
        {workspace && (
          <nav aria-label="主な画面" className="hidden min-w-0 items-center overflow-x-auto [scrollbar-width:none] sm:flex">
            {SECTIONS.map((s) => (
              <Link
                key={s}
                to={sectionPath(workspace.slug, s)}
                aria-current={section === s ? 'page' : undefined}
                className={cn(
                  'shrink-0 rounded-full px-2.5 py-1.5 text-sm text-ink-3 transition-colors hover:text-foreground md:px-3',
                  section === s && 'font-semibold text-foreground',
                )}
              >
                {SECTION_LABELS[s]}
              </Link>
            ))}
          </nav>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onOpenPalette}
            className="hidden items-center gap-1.5 rounded-md border bg-secondary px-2 py-1 font-mono text-xs text-ink-3 shadow-soft md:inline-flex"
            aria-label="コマンドパレットを開く"
          >
            ⌘K
          </button>
          <span className="hidden sm:inline-flex">
            <ThemeToggle />
          </span>
          <AccountMenu />
        </div>
      </div>
    </header>
  )
}
