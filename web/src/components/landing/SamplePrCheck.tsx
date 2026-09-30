import { Panel } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { useReveal } from '@/hooks/useReveal'
import { cn } from '@/lib/utils'
import { SAMPLE_PR } from '@/services/landingContent'
import { Sample } from './Sample'

export function SamplePrCheck() {
  const shown = useReveal(SAMPLE_PR.checks.length, 900)
  return (
    <Sample label="PR の検査結果の見本: 要求ごとに適合・違反・未検証が確定していく">
      <Panel className="space-y-2 p-4 shadow-lift">
        <p className="text-xs text-ink-3">{SAMPLE_PR.label}</p>
        {SAMPLE_PR.checks.map((c, i) => {
          const done = i < shown
          const bad = done && c.verdict === 'VIOLATED'
          return (
            <div
              key={c.id}
              className={cn('rounded-lg bg-card px-3 py-2 transition-colors', bad && 'bg-bad-bg')}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <code className="font-mono text-xs">{c.id}</code>
                <span className="min-w-0 flex-1 truncate text-sm">{c.title}</span>
                {done ? (
                  <VerdictBadge verdict={c.verdict} />
                ) : (
                  <span className="text-xs text-ink-3 motion-safe:animate-pulse">検査中…</span>
                )}
              </div>
              {bad && c.counterexample && (
                <p className="mt-1 font-mono text-[11px] break-all text-ink-2">{c.counterexample}</p>
              )}
            </div>
          )
        })}
      </Panel>
    </Sample>
  )
}
