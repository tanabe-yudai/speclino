import { Link } from 'react-router-dom'
import { ThemeToggle } from '@/components/shell/ThemeToggle'
import { NAV } from '@/services/landingContent'

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <a href="#top" className="shrink-0 font-extrabold tracking-tight">
          <span className="text-brand" aria-hidden="true">◆</span> SpecLino
        </a>
        <nav className="ml-6 hidden gap-4 text-sm text-ink-3 sm:flex">
          <a className="hover:text-foreground" href="#start">{NAV.start}</a>
          <a className="hover:text-foreground" href="#principles">{NAV.principles}</a>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <Link to="/login" className="rounded-lg bg-card px-3.5 py-1.5 text-sm font-semibold shadow-soft transition-shadow hover:shadow-lift">
            {NAV.login}
          </Link>
        </div>
      </div>
    </header>
  )
}
