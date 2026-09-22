import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getReviewMarkdown, getRun, getRunEvents } from '../api/reviewRuns'
import { ApiError } from '../api/errors'
import { Badge } from '../components/Badge'
import { ErrorState } from '../components/ErrorState'
import { FindingCard } from '../components/FindingCard'
import { LoadingState } from '../components/LoadingState'
import { ScopeBlock } from '../components/ScopeBlock'
import { Timeline } from '../components/Timeline'
import { describeGateReason } from '../services/gate'
import { formatDateTime, shortSha } from '../services/format'
import { githubChecksUrl, githubPrUrl } from '../services/github'
import { EXEC_STATE_LABELS, GATE_STATUS_LABELS, GATE_TONE, VERDICT_LABELS } from '../services/labels'
import { describeLimits, describeSemanticDiff } from '../services/manifest'
import { collectScopeAndAssumptions, collectUnresolvedItems, countByVerdict, groupFindingsByVerdict } from '../services/verdict'
import type { RunEvent } from '../types/events'
import type { ReviewRunDetail } from '../types/run'

type Tab = 'timeline' | 'markdown'

const DETAIL_VERDICTS = ['VIOLATED', 'REVIEW', 'UNKNOWN'] as const

export function RunDetail() {
  const { id } = useParams<{ id: string }>()
  if (!id) {
    return <ErrorState message="run ID が指定されていません" />
  }
  // id ごとに key で強制的に再マウントする。前の run の状態を引きずらず、
  // effect 内での明示的なリセット（setState）も不要になる。
  return <RunDetailView key={id} id={id} />
}

function RunDetailView({ id }: { id: string }) {
  const [run, setRun] = useState<ReviewRunDetail | null>(null)
  const [events, setEvents] = useState<RunEvent[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [tab, setTab] = useState<Tab>('timeline')
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [markdownLoading, setMarkdownLoading] = useState(false)
  const [markdownError, setMarkdownError] = useState<string | null>(null)

  const fetchRun = useCallback(() => {
    Promise.all([getRun(id), getRunEvents(id)])
      .then(([runData, eventsData]) => {
        setRun(runData)
        setEvents(eventsData)
      })
      .catch((err: unknown) => {
        setLoadError(err instanceof ApiError ? err.message : 'run の取得に失敗しました')
      })
  }, [id])

  useEffect(() => {
    fetchRun()
  }, [fetchRun])

  function retryRun() {
    setRun(null)
    setEvents(null)
    setLoadError(null)
    fetchRun()
  }

  const fetchMarkdown = useCallback(() => {
    setMarkdownLoading(true)
    setMarkdownError(null)
    getReviewMarkdown(id)
      .then(setMarkdown)
      .catch((err: unknown) => {
        setMarkdownError(err instanceof ApiError ? err.message : 'review.md の取得に失敗しました')
      })
      .finally(() => setMarkdownLoading(false))
  }, [id])

  // review.md はタブを開いたとき（ユーザー操作起点）にだけ取得する。まだ完了していない
  // run では成果物が存在しない場合もあるため、必要になるまで fetch しない。
  function selectTab(next: Tab) {
    setTab(next)
    if (next === 'markdown' && markdown === null && !markdownLoading && !markdownError) {
      fetchMarkdown()
    }
  }

  function retryMarkdown() {
    setMarkdown(null)
    fetchMarkdown()
  }

  return (
    <main className="run-detail-page">
      <Link to="/" className="back-link">
        ← run 一覧へ戻る
      </Link>

      {run === null && loadError === null && <LoadingState message="run を取得しています…" />}
      {loadError && <ErrorState message={loadError} onRetry={retryRun} />}

      {run && (
        <>
          <RunSummary run={run} />

          <section className="findings-section">
            <h2>判定結果</h2>
            <FindingsByVerdict run={run} />
          </section>

          <section className="run-detail-tabs">
            <div className="tab-bar" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'timeline'}
                className={tab === 'timeline' ? 'tab-active' : ''}
                onClick={() => selectTab('timeline')}
              >
                タイムライン
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'markdown'}
                className={tab === 'markdown' ? 'tab-active' : ''}
                onClick={() => selectTab('markdown')}
              >
                review.md
              </button>
            </div>

            {tab === 'timeline' &&
              (events !== null ? <Timeline events={events} /> : <LoadingState message="イベントを取得しています…" />)}

            {tab === 'markdown' && (
              <>
                {markdownLoading && <LoadingState message="review.md を取得しています…" />}
                {markdownError && <ErrorState message={markdownError} onRetry={retryMarkdown} />}
                {markdown !== null && !markdownLoading && !markdownError && (
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}
    </main>
  )
}

function RunSummary({ run }: { run: ReviewRunDetail }) {
  const counts = countByVerdict(run.findings)
  const unverifiedCount = counts.UNKNOWN + counts.REVIEW
  const { assumptions } = collectScopeAndAssumptions(run.findings)
  const unresolvedItems = collectUnresolvedItems(run.findings)
  const scopeItems = run.manifest ? [describeLimits(run.manifest)] : []
  const semanticDiff = run.manifest ? describeSemanticDiff(run.manifest) : null
  const gateReason = describeGateReason(run.gate_status, run.manifest, run.findings)
  const canLinkGithub = Boolean(run.repo_full_name && run.pr_number != null)

  return (
    <section className="run-summary">
      <div className="run-summary-header">
        <div>
          <h1>
            {run.repo_full_name ?? '(repo 不明)'}
            {run.pr_number != null && `#${run.pr_number}`}
          </h1>
          <p className="muted">
            head <code title={run.head_sha}>{shortSha(run.head_sha)}</code>
            {' ← '}
            base <code title={run.base_sha ?? undefined}>{shortSha(run.base_sha)}</code>
          </p>
        </div>
        {run.gate_status ? (
          <Badge tone={GATE_TONE[run.gate_status]}>{GATE_STATUS_LABELS[run.gate_status]}</Badge>
        ) : (
          <Badge tone="neutral">未確定（{EXEC_STATE_LABELS[run.exec_state]}）</Badge>
        )}
      </div>

      <p className="gate-reason">{gateReason}</p>
      {semanticDiff && <p className="muted run-meta-line">{semanticDiff}</p>}

      <dl className="run-counts">
        <div>
          <dt>{VERDICT_LABELS.VIOLATED}</dt>
          <dd>{counts.VIOLATED}</dd>
        </div>
        <div>
          <dt>未検証（要確認 {counts.REVIEW} ・ 不明 {counts.UNKNOWN}）</dt>
          <dd>{unverifiedCount}</dd>
        </div>
        <div>
          <dt>{VERDICT_LABELS.VERIFIED_IN_SCOPE}</dt>
          <dd>{counts.VERIFIED_IN_SCOPE}</dd>
        </div>
      </dl>

      <div className="run-links">
        {canLinkGithub ? (
          <>
            <a href={githubPrUrl(run.repo_full_name!, run.pr_number!)} target="_blank" rel="noreferrer">
              GitHub の PR を開く
            </a>
            <a href={githubChecksUrl(run.repo_full_name!, run.pr_number!)} target="_blank" rel="noreferrer">
              Check を開く
            </a>
          </>
        ) : (
          <span className="muted">GitHub へのリンクなし（PR 情報が未設定）</span>
        )}
      </div>

      <p className="muted run-meta-line">
        作成: {formatDateTime(run.created_at)} ／ 完了: {formatDateTime(run.finished_at)} ／ トリガー: {run.trigger}
      </p>

      <ScopeBlock
        title="保証範囲"
        scopeItems={scopeItems}
        assumptions={assumptions}
        unresolvedItems={unresolvedItems}
      />
    </section>
  )
}

function FindingsByVerdict({ run }: { run: ReviewRunDetail }) {
  if (run.findings.length === 0) {
    return <p className="muted">この run にはまだ Finding がありません（実行中、または未完了の可能性があります）。</p>
  }
  const grouped = groupFindingsByVerdict(run.findings)
  return (
    <>
      {DETAIL_VERDICTS.map((verdict) => {
        const items = grouped[verdict]
        if (items.length === 0) return null
        return (
          <div key={verdict} className="finding-group">
            <h3>
              {VERDICT_LABELS[verdict]}（{items.length}件）
            </h3>
            {items.map((finding) => (
              <FindingCard
                key={finding.id}
                finding={finding}
                repoFullName={run.repo_full_name}
                headSha={run.head_sha}
              />
            ))}
          </div>
        )
      })}

      {grouped.VERIFIED_IN_SCOPE.length > 0 && (
        <details className="finding-group finding-group-verified">
          <summary>
            {VERDICT_LABELS.VERIFIED_IN_SCOPE}（{grouped.VERIFIED_IN_SCOPE.length}件） — 展開して表示
          </summary>
          {grouped.VERIFIED_IN_SCOPE.map((finding) => (
            <FindingCard key={finding.id} finding={finding} repoFullName={run.repo_full_name} headSha={run.head_sha} />
          ))}
        </details>
      )}
    </>
  )
}
