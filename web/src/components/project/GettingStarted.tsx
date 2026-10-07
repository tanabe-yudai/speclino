import { Check, CircleDashed, CircleHelp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getOnboarding } from '@/api/projectSettings'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ONBOARDING_STATE_LABELS, ONBOARDING_STEP_TITLES, onboardingDone } from '@/services/projectSettings'
import type { OnboardingStep } from '@/types/projectSettings'

const STEP_ICON = { done: Check, todo: CircleDashed, unknown: CircleHelp } as const

function Steps({ steps }: { steps: OnboardingStep[] }) {
  return (
    <ol className="space-y-3">
      {steps.map((step, i) => {
        const Icon = STEP_ICON[step.state]
        return (
          <li key={step.id} className="flex gap-3">
            <Icon
              aria-hidden
              className={step.state === 'done' ? 'mt-0.5 size-4 shrink-0 text-ok' : 'mt-0.5 size-4 shrink-0 text-ink-3'}
            />
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                <span>
                  {i + 1}. {ONBOARDING_STEP_TITLES[step.id]}
                </span>
                {step.state === 'done' ? (
                  <ToneBadge tone="ok">{ONBOARDING_STATE_LABELS.done}</ToneBadge>
                ) : (
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-2">
                    {ONBOARDING_STATE_LABELS[step.state]}
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-xs break-words text-ink-3">{step.detail}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** まだ検査されていないプロジェクトの概要に出す、はじめ方（到達状態）。取れないときは何も出さない。 */
export function GettingStarted({ slug, fullName }: { slug: string; fullName: string }) {
  const [steps, setSteps] = useState<OnboardingStep[] | null>(null)

  useEffect(() => {
    let cancelled = false
    getOnboarding(slug)
      .then((o) => {
        if (!cancelled) setSteps(o.projects.find((p) => p.repo_full_name === fullName)?.steps ?? null)
      })
      .catch(() => {
        if (!cancelled) setSteps(null)
      })
    return () => {
      cancelled = true
    }
  }, [slug, fullName])

  if (!steps) return null
  return (
    <Panel className="space-y-3">
      <PanelTitle className="text-sm">{onboardingDone(steps) ? 'はじめ方（完了）' : 'はじめ方'}</PanelTitle>
      <Steps steps={steps} />
    </Panel>
  )
}
