import { Link, NavLink } from 'react-router-dom'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { cn } from '@/lib/utils'
import { paths } from '@/services/paths'
import { AccountMenu } from './AccountMenu'
import { ProjectPill } from './ProjectPill'
import { ThemeToggle } from './ThemeToggle'
import { WorkspacePill } from './WorkspacePill'

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn('rounded-full px-3 py-1.5 text-sm text-ink-3 transition-colors hover:text-foreground', isActive && 'text-foreground font-semibold')

/** ログイン後の上部に固定するバー（design 3.3）。 */
export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const workspace = useCurrentWorkspace()
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <Link to={paths.home()} className="mr-1 shrink-0 font-extrabold tracking-tight">
          <span className="text-brand">◆</span> SpecLino
        </Link>
        <WorkspacePill />
        <ProjectPill />
        {workspace && (
          <NavLink to={paths.settings(workspace.slug)} className={navClass}>
            設定
          </NavLink>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={onOpenPalette}
            className="hidden items-center gap-1.5 rounded-md border bg-secondary px-2 py-1 font-mono text-xs text-ink-3 shadow-soft sm:inline-flex"
            aria-label="コマンドパレットを開く"
          >
            ⌘K
          </button>
          <ThemeToggle />
          <AccountMenu />
        </div>
      </div>
    </header>
  )
}
