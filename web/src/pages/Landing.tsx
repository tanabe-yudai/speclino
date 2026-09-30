import { Navigate } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { LandingHeader } from '@/components/landing/LandingHeader'
import { SampleMcp } from '@/components/landing/SampleMcp'
import { SamplePrCheck } from '@/components/landing/SamplePrCheck'
import { SampleProject } from '@/components/landing/SampleProject'
import { SampleRepo } from '@/components/landing/SampleRepo'
import { SampleSpec } from '@/components/landing/SampleSpec'
import { HERO, INVITE_ONLY, NAV, PRINCIPLES, STEPS } from '@/services/landingContent'
import { landingDecision } from '@/services/landingRoute'

const STEP_SAMPLES = [SampleRepo, SampleSpec, SampleMcp, SampleProject]

/** ランディングページ（S58、design 7 章）。ログイン済みならアプリへ。 */
export function Landing() {
  const { user, loading } = useAuth()
  const decision = landingDecision(loading, user !== null)
  if (decision === 'wait') return null
  if (decision === 'app') return <Navigate to="/app" replace />

  return (
    <div id="top" className="min-h-screen">
      <LandingHeader />
      <main className="mx-auto w-full max-w-6xl px-4 sm:px-6">
        <section className="grid items-center gap-10 py-14 sm:py-20 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)]">
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-[0.12em] text-primary">{HERO.eyebrow}</p>
            <h1 className="mt-3 text-4xl leading-tight font-extrabold tracking-tight sm:text-5xl">
              {HERO.titleLead}
              <br />
              <span className="text-brand">{HERO.titleAccent}</span>
            </h1>
            <p className="mt-5 max-w-xl leading-relaxed text-ink-2">{HERO.body}</p>
            <a href="#start" className="mt-7 inline-flex items-center gap-1 rounded-xl bg-card px-5 py-2.5 font-semibold shadow-soft transition-shadow hover:shadow-lift">
              {HERO.cta} <span aria-hidden="true">↓</span>
            </a>
          </div>
          <SamplePrCheck />
        </section>

        <section id="start" className="scroll-mt-20 py-12">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">GET STARTED</p>
          <h2 className="mt-1 text-2xl font-extrabold">{NAV.startHeading}</h2>
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

        <section id="principles" className="scroll-mt-20 py-12">
          <h2 className="text-2xl font-extrabold">{NAV.principlesHeading}</h2>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {PRINCIPLES.map((p) => (
              <div key={p.title} className="rounded-2xl bg-card/70 p-5 shadow-soft">
                <p className="font-bold">{p.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-ink-2">{p.body}</p>
              </div>
            ))}
          </div>
        </section>

        <footer className="py-12 text-center text-sm text-ink-3">{INVITE_ONLY}</footer>
      </main>
    </div>
  )
}
