import { ToneBadge } from '@/components/app/ToneBadge'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { cn } from '@/lib/utils'
import { CounterexamplePanel } from './CounterexamplePanel'
import { githubBlobUrl } from '../services/github'
import {
  BASE_HEAD_STATUS_LABELS,
  BASE_HEAD_STATUS_TONE,
  CATEGORY_LABELS,
  SEVERITY_LABELS,
  TEST_OBSERVATION_LABELS,
} from '../services/labels'
import type { FindingCardProps } from '../types/components'
import type { SourceRef } from '../types/finding'

function refLabel(ref: SourceRef, preferSymbol: boolean): string {
  const primary = preferSymbol ? (ref.symbol ?? ref.requirement_id) : (ref.requirement_id ?? ref.symbol)
  let label = primary ?? ref.path ?? ref.snapshot_id
  if (ref.line_start != null) {
    label += `:${ref.line_start}`
    if (ref.line_end != null && ref.line_end !== ref.line_start) label += `-${ref.line_end}`
  }
  return label
}

interface RefListProps {
  refs: SourceRef[]
  preferSymbol: boolean
  repoFullName: string | null
  headSha: string
}

function RefList({ refs, preferSymbol, repoFullName, headSha }: RefListProps) {
  if (refs.length === 0) return <span className="text-ink-3">（記録なし）</span>
  return (
    <ul className="space-y-0.5">
      {refs.map((ref, index) => {
        const label = refLabel(ref, preferSymbol)
        return (
          <li key={`${ref.path ?? ref.requirement_id ?? ref.symbol ?? index}-${index}`} className="font-mono text-xs break-all">
            {repoFullName && ref.path ? (
              <a
                className="text-primary underline-offset-4 hover:underline"
                href={githubBlobUrl(repoFullName, headSha, ref.path, ref.line_start, ref.line_end)}
                target="_blank"
                rel="noreferrer"
              >
                {label}
              </a>
            ) : (
              label
            )}
          </li>
        )
      })}
    </ul>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-semibold text-ink-3">{label}</p>
      <div className="text-sm text-ink-2">{children}</div>
    </div>
  )
}

/** 1 つの要求の判定。違反は左端に細い線を入れる。 */
export function FindingCard({ finding, repoFullName, headSha }: FindingCardProps) {
  const needsNextSteps = finding.verdict === 'UNKNOWN' || finding.verdict === 'REVIEW'
  const base = finding.base_head_status
  return (
    <article className={cn('row space-y-4 p-5', finding.verdict === 'VIOLATED' && 'border-l-4 border-l-bad')}>
      <header className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 font-mono text-sm font-semibold break-all">{finding.requirement_id}</code>
        <VerdictBadge verdict={finding.verdict} />
        {base && base !== 'unchanged' && (
          <ToneBadge tone={BASE_HEAD_STATUS_TONE[base]}>base 比: {BASE_HEAD_STATUS_LABELS[base]}</ToneBadge>
        )}
        <span className="ml-auto text-xs text-ink-3">
          {CATEGORY_LABELS[finding.category]} ・ 重要度 {SEVERITY_LABELS[finding.severity]}
        </span>
      </header>

      <p className="text-sm leading-relaxed">{finding.message}</p>

      {(finding.expected || finding.actual) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="仕様の期待">{finding.expected ?? '（記録なし）'}</Field>
          <Field label="実装">{finding.actual ?? '（記録なし）'}</Field>
        </div>
      )}

      {finding.counterexample && <CounterexamplePanel counterexample={finding.counterexample} />}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="仕様参照">
          <RefList refs={finding.spec_refs} preferSymbol={false} repoFullName={repoFullName} headSha={headSha} />
        </Field>
        <Field label="コード参照">
          <RefList refs={finding.code_refs} preferSymbol repoFullName={repoFullName} headSha={headSha} />
        </Field>
      </div>

      {(finding.analysis_scope || finding.assumptions.length > 0 || finding.test_observation) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {finding.analysis_scope && <Field label="解析範囲">{finding.analysis_scope}</Field>}
          {finding.assumptions.length > 0 && <Field label="仮定">{finding.assumptions.join('; ')}</Field>}
          {finding.test_observation && (
            <Field label="テスト観測">{TEST_OBSERVATION_LABELS[finding.test_observation]}</Field>
          )}
        </div>
      )}

      {needsNextSteps && (
        <Field label="次にできること">
          {finding.next_steps.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5">
              {finding.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          ) : (
            '（記録なし）'
          )}
        </Field>
      )}
    </article>
  )
}
