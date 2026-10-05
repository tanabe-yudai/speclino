import { Link, NavLink } from 'react-router-dom'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { cn } from '@/lib/utils'
import { paths } from '@/services/paths'
import { AccountMenu } from './AccountMenu'
import { ProjectPill } from './ProjectPill'
import { ThemeToggle } from './ThemeToggle'
import { WorkspacePill } from './WorkspacePill'

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn('shrink-0 rounded-full px-3 py-1.5 text-sm text-ink-3 transition-colors hover:text-foreground', isActive && 'text-foreground font-semibold')

/** ログイン後の上部に固定するバー（design 3.3）。 */
export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const workspace = useCurrentWorkspace()
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <Link to={paths.home()} className="mr-1 shrink-0 font-extrabold tracking-tight">
          <span className="text-brand">◆</span>
          <span className="hidden sm:inline"> SpecLino</span>
        </Link>
        <WorkspacePill />
        <ProjectPill />
        {workspace && (
          <>
            {/* スマホ幅ではアカウントメニューに入れる（ピルの幅を残す） */}
            <NavLink to={paths.knowledgeRoot(workspace.slug)} className={(s) => cn(navClass(s), 'hidden sm:inline-flex')}>
              知識
            </NavLink>
            <NavLink to={paths.policies(workspace.slug)} className={(s) => cn(navClass(s), 'hidden sm:inline-flex')}>
              規約
            </NavLink>
            <NavLink to={paths.observability(workspace.slug)} className={(s) => cn(navClass(s), 'hidden sm:inline-flex')}>
              可観測性
            </NavLink>
            <NavLink to={paths.settings(workspace.slug)} className={(s) => cn(navClass(s), 'hidden sm:inline-flex')}>
              設定
            </NavLink>
          </>
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
          <span className="hidden sm:inline-flex">
            <ThemeToggle />
          </span>
          <AccountMenu />
        </div>
      </div>
    </header>
  )
}
