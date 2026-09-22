import { Badge } from './Badge'
import { CounterexamplePanel } from './CounterexamplePanel'
import { githubBlobUrl } from '../services/github'
import {
  BASE_HEAD_STATUS_LABELS,
  BASE_HEAD_STATUS_TONE,
  CATEGORY_LABELS,
  SEVERITY_LABELS,
  TEST_OBSERVATION_LABELS,
  VERDICT_LABELS,
  VERDICT_TONE,
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
  emptyText: string
}

function RefList({ refs, preferSymbol, repoFullName, headSha, emptyText }: RefListProps) {
  if (refs.length === 0) return <span className="muted">{emptyText}</span>
  return (
    <ul className="ref-list">
      {refs.map((ref, index) => {
        const label = refLabel(ref, preferSymbol)
        return (
          <li key={`${ref.path ?? ref.requirement_id ?? ref.symbol ?? index}-${index}`}>
            {repoFullName && ref.path ? (
              <a
                href={githubBlobUrl(repoFullName, headSha, ref.path, ref.line_start, ref.line_end)}
                target="_blank"
                rel="noreferrer"
              >
                {label}
              </a>
            ) : (
              <code>{label}</code>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function FindingCard({ finding, repoFullName, headSha }: FindingCardProps) {
  const needsNextSteps = finding.verdict === 'UNKNOWN' || finding.verdict === 'REVIEW'
  return (
    <article className={`finding-card finding-card-${finding.verdict.toLowerCase()}`}>
      <header className="finding-card-header">
        <Badge tone={VERDICT_TONE[finding.verdict]}>{VERDICT_LABELS[finding.verdict]}</Badge>
        {finding.base_head_status && finding.base_head_status !== 'unchanged' && (
          <Badge tone={BASE_HEAD_STATUS_TONE[finding.base_head_status]}>
            base 比: {BASE_HEAD_STATUS_LABELS[finding.base_head_status]}
          </Badge>
        )}
        <span className="finding-requirement">{finding.requirement_id}</span>
        <span className="finding-category">{CATEGORY_LABELS[finding.category]}</span>
        <span className="finding-severity">重要度: {SEVERITY_LABELS[finding.severity]}</span>
      </header>

      <p className="finding-message">{finding.message}</p>

      {(finding.expected || finding.actual) && (
        <div className="finding-expected-actual">
          <div>
            <span className="field-label">期待</span>
            <p>{finding.expected ?? '（記録なし）'}</p>
          </div>
          <div>
            <span className="field-label">実装</span>
            <p>{finding.actual ?? '（記録なし）'}</p>
          </div>
        </div>
      )}

      {finding.counterexample && <CounterexamplePanel counterexample={finding.counterexample} />}

      <div className="finding-refs">
        <div>
          <span className="field-label">仕様参照</span>
          <RefList
            refs={finding.spec_refs}
            preferSymbol={false}
            repoFullName={repoFullName}
            headSha={headSha}
            emptyText="（記録なし）"
          />
        </div>
        <div>
          <span className="field-label">コード参照</span>
          <RefList
            refs={finding.code_refs}
            preferSymbol
            repoFullName={repoFullName}
            headSha={headSha}
            emptyText="（記録なし）"
          />
        </div>
      </div>

      {(finding.analysis_scope || finding.assumptions.length > 0) && (
        <div className="finding-meta">
          {finding.analysis_scope && (
            <p>
              <span className="field-label">解析範囲</span> {finding.analysis_scope}
            </p>
          )}
          {finding.assumptions.length > 0 && (
            <p>
              <span className="field-label">仮定</span> {finding.assumptions.join('; ')}
            </p>
          )}
        </div>
      )}

      {finding.test_observation && (
        <p className="finding-test-observation">
          <span className="field-label">テスト観測</span> {TEST_OBSERVATION_LABELS[finding.test_observation]}
        </p>
      )}

      {needsNextSteps && (
        <div className="finding-next-steps">
          <span className="field-label">次にできること</span>
          {finding.next_steps.length > 0 ? (
            <ul>
              {finding.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">（記録なし）</p>
          )}
        </div>
      )}
    </article>
  )
}
