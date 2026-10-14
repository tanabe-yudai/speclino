import { BadgeCheck, CircleHelp, History, PenLine, type LucideIcon } from 'lucide-react'
import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { AgentSystem } from '@/components/landing/AgentSystem'
import { AnchorLink } from '@/components/landing/AnchorLink'
import { GitHubMark } from '@/components/landing/GitHubMark'
import { LandingHeader } from '@/components/landing/LandingHeader'
import { SampleApprove } from '@/components/landing/SampleApprove'
import { SampleMcp } from '@/components/landing/SampleMcp'
import { SamplePrCheck } from '@/components/landing/SamplePrCheck'
import { SampleRepo } from '@/components/landing/SampleRepo'
import { SampleSession } from '@/components/landing/SampleSession'
import { AGENTS_SECTION, HERO, INVITE_ONLY, NAV, OPEN_SOURCE, REPO_URL, STEPS, TRUST, TRUST_ITEMS } from '@/services/landingContent'
import { landingDecision } from '@/services/landingRoute'
import type { TrustItem } from '@/types/landing'

const STEP_SAMPLES = [SampleRepo, SampleApprove, SampleMcp, SamplePrCheck]

const TRUST_ICON: Record<TrustItem['key'], LucideIcon> = {
  'fail-closed': CircleHelp,
  'ai-proposes': PenLine,
  'approved-only': BadgeCheck,
  traceable: History,
}

/** ランディングページ。ログイン済みならアプリへ。 */
export function Landing() {
  const { user, loading } = useAuth()
  const decision = landingDecision(loading, user !== null)

  // 認証の確認中は何も描かないので、/#start などを直接開いたときは描いてから位置を合わせる
  useEffect(() => {
    if (decision !== 'landing' || !window.location.hash) return
    document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView()
  }, [decision])

  if (decision === 'wait') return null
  if (decision === 'app') return <Navigate to="/app" replace />

  return (
    <div id="top" className="min-h-screen">
      <LandingHeader />
      <main className="mx-auto w-full max-w-6xl px-4 [overflow-wrap:anywhere] sm:px-6">
        <section className="grid items-center gap-10 py-14 sm:py-20 lg:grid-cols-[minmax(0,1fr)_minmax(0,30rem)]">
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-[0.12em] text-primary">{HERO.eyebrow}</p>
            <h1 className="mt-3 text-[1.75rem] leading-tight font-extrabold tracking-tight sm:text-5xl">
              {HERO.titleLead}
              <br />
              <span className="text-brand">{HERO.titleAccent}</span>
            </h1>
            <p className="mt-5 max-w-xl leading-relaxed text-ink-2">{HERO.body}</p>
            <AnchorLink href="#agents" className="mt-7 inline-flex items-center gap-1 rounded-xl bg-card px-5 py-2.5 font-semibold shadow-soft transition-shadow hover:shadow-lift">
              {HERO.cta} <span aria-hidden="true">↓</span>
            </AnchorLink>
          </div>
          <SampleSession />
        </section>

        <section id="agents" className="scroll-mt-20 py-12">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">{AGENTS_SECTION.eyebrow}</p>
          <h2 className="mt-1 text-2xl font-extrabold">{AGENTS_SECTION.heading}</h2>
          <p className="mt-3 max-w-3xl leading-relaxed text-ink-2">{AGENTS_SECTION.lead}</p>
          <AgentSystem />
        </section>

        <section id="start" className="scroll-mt-20 py-12">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">GET STARTED</p>
          <h2 className="mt-1 text-2xl font-extrabold">{NAV.start}</h2>
          <ol className="mt-8 space-y-12">
            {STEPS.map((s, i) => {
              const SampleView = STEP_SAMPLES[i]
              return (
                <li key={s.number} className="grid items-center gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                  <div className="min-w-0">
                    <p className="text-3xl font-extrabold text-ink-3/40">{s.number}</p>
                    <h3 className="mt-1 text-lg font-bold">{s.title}</h3>
                    <p className="mt-2 leading-relaxed text-ink-2">{s.body}</p>
                  </div>
                  <SampleView />
                </li>
              )
            })}
          </ol>
        </section>

        <section id="trust" className="scroll-mt-20 py-12">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">TRUST</p>
          <h2 className="mt-1 text-2xl font-extrabold">{TRUST.heading}</h2>
          <p className="mt-3 max-w-3xl leading-relaxed text-ink-2">{TRUST.lead}</p>
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {TRUST_ITEMS.map((t) => {
              const Icon = TRUST_ICON[t.key]
              return (
                <div key={t.key} className="flex gap-4 rounded-2xl bg-card/70 p-6 shadow-soft">
                  <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent text-primary">
                    <Icon className="size-6" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <h3 className="font-bold">{t.title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-ink-2">{t.body}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      </main>
      <footer className="space-y-2 py-12 text-center text-sm text-ink-3">
        <p>{INVITE_ONLY}</p>
        <p>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 underline-offset-4 hover:text-foreground hover:underline">
            <GitHubMark />
            {OPEN_SOURCE}
          </a>
        </p>
      </footer>
    </div>
  )
}
