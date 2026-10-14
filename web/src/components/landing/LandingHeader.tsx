import { Link } from 'react-router-dom'
import { ThemeToggle } from '@/components/shell/ThemeToggle'
import { NAV, REPO_URL } from '@/services/landingContent'
import { BrandMark } from '@/components/shell/BrandMark'
import { AnchorLink } from './AnchorLink'
import { GitHubMark } from './GitHubMark'

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <AnchorLink href="#top" className="shrink-0 font-extrabold tracking-tight">
          <BrandMark /> SpecLino
        </AnchorLink>
        <nav className="ml-6 hidden gap-4 text-sm text-ink-3 sm:flex">
          <AnchorLink className="hover:text-foreground" href="#agents">{NAV.agents}</AnchorLink>
          <AnchorLink className="hover:text-foreground" href="#start">{NAV.start}</AnchorLink>
          <AnchorLink className="hover:text-foreground" href="#trust">{NAV.trust}</AnchorLink>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-ink-3 hover:text-foreground">
            <GitHubMark className="size-5" />
            <span className="sr-only sm:not-sr-only">{NAV.github}</span>
          </a>
          <ThemeToggle />
          <Link to="/login" className="rounded-lg bg-card px-3.5 py-1.5 text-sm font-semibold shadow-soft transition-shadow hover:shadow-lift">
            {NAV.login}
          </Link>
        </div>
      </div>
    </header>
  )
}
