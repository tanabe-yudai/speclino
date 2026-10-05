import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getReviewMarkdown, getRun, getRunEvents, getSupplements } from '../api/reviewRuns'
import { ApiError } from '../api/errors'
import { ErrorState } from '../components/ErrorState'
import { AttestButton } from '../components/AttestButton'
import { FindingCard } from '../components/FindingCard'
import { LoadingState } from '../components/LoadingState'
import { RerunButton } from '../components/RerunButton'
import { ScopeBlock } from '../components/ScopeBlock'
import { SupplementList } from '../components/SupplementList'
import { Timeline } from '../components/Timeline'
import {
  attestationTarget,
  canAttest,
  configChangeWarning,
  describeAttestation,
  prCommentCommand,
  recordedAttestation,
  requiresPrComment,
} from '../services/attestation'
import { describeGateReason } from '../services/gate'
import { useMe } from '../hooks/useMe'
import { formatDateTime, shortSha } from '../services/format'
import { githubChecksUrl, githubPrUrl } from '../services/github'
import { EXEC_STATE_LABELS, VERDICT_LABELS } from '../services/labels'
import { GateBadge } from '@/components/app/GateBadge'
import { Panel } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { paths } from '@/services/paths'
import { describeLimits, describeSemanticDiff, describeTeamPolicy } from '../services/manifest'
import { collectScopeAndAssumptions, collectUnresolvedItems, countByVerdict, groupFindingsByVerdict } from '../services/verdict'
import type { RunEvent } from '../types/events'
import type { FindingAttestProps } from '../types/components'
import type { RerunResponse, ReviewRunDetail } from '../types/run'
import type { Verdict } from '../types/finding'
import type { AgentSupplement } from '../types/supplement'

type Tab = 'timeline' | 'markdown'

const OPEN_STATES = new Set(['queued', 'running', 'awaiting_answer'])
const POLL_INTERVAL_MS = 2000

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
  const [supplements, setSupplements] = useState<AgentSupplement[]>([])
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
    // 補足は付随情報。取れなくても本体の表示は止めない（取れなければ前回の表示を保つ）
    getSupplements(id)
      .then(setSupplements)
      .catch(() => {})
  }, [id])

  useEffect(() => {
    fetchRun()
  }, [fetchRun])

  // 未完了の run は完了するまで数秒おきに取り直す（再実行ボタン直後に待たせない）
  const inProgress = run !== null && OPEN_STATES.has(run.exec_state)
  useEffect(() => {
    if (!inProgress) return
    const timer = window.setTimeout(fetchRun, POLL_INTERVAL_MS)
    return () => window.clearTimeout(timer)
  }, [inProgress, run, fetchRun])

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
    <div className="space-y-8">
      <Link to={paths.home()} className="text-sm text-ink-3 hover:text-foreground">
        ← ホームへ
      </Link>

      {run === null && loadError === null && <LoadingState message="検査を取得しています…" />}
      {loadError && <ErrorState message={loadError} onRetry={retryRun} />}

      {run && (
        <>
          <RunSummary run={run} />

          <section className="space-y-4">
            <h2 className="text-lg font-semibold tracking-tight">判定結果</h2>
            <FindingsByVerdict run={run} supplements={supplements} />
          </section>

          <Tabs value={tab} onValueChange={(v) => selectTab(v as Tab)}>
            <TabsList>
              <TabsTrigger value="timeline">タイムライン</TabsTrigger>
              <TabsTrigger value="markdown">レビュー（review.md）</TabsTrigger>
            </TabsList>
            <TabsContent value="timeline" className="mt-4">
              <Panel>
                {events !== null ? <Timeline events={events} /> : <LoadingState message="イベントを取得しています…" />}
              </Panel>
            </TabsContent>
            <TabsContent value="markdown" className="mt-4">
              {markdownLoading && <LoadingState message="review.md を取得しています…" />}
              {markdownError && <ErrorState message={markdownError} onRetry={retryMarkdown} />}
              {markdown !== null && !markdownLoading && !markdownError && (
                <Panel className="overflow-x-auto text-sm leading-relaxed [overflow-wrap:break-word] [&_table]:min-w-full [&_td]:align-top [&_a]:text-primary [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-bold [&_h2]:mt-6 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-4 [&_h3]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_table]:my-3 [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:px-2 [&_th]:py-1">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                </Panel>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  )
}

function RunSummary({ run }: { run: ReviewRunDetail }) {
  const navigate = useNavigate()
  // 再実行ボタンから遷移してきたときは、回数・費用枠の情報を router state で受け取る
  const rerun = (useLocation().state as { rerun?: RerunResponse } | null)?.rerun
  const counts = countByVerdict(run.findings)
  const { assumptions } = collectScopeAndAssumptions(run.findings)
  const unresolvedItems = collectUnresolvedItems(run.findings)
  const scopeItems = run.manifest ? [describeLimits(run.manifest), describeTeamPolicy(run.manifest)] : []
  const semanticDiff = run.manifest ? describeSemanticDiff(run.manifest) : null
  const gateReason = describeGateReason(run.gate_status, run.manifest, run.findings)
  const canLinkGithub = Boolean(run.repo_full_name && run.pr_number != null)

  return (
    <div className="space-y-4">
      <Panel className="space-y-5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            {/* オーナー名は小さく、リポジトリ名を大きく。どちらも省略せず折り返す */}
            {run.repo_full_name && (
              <p className="font-mono text-xs break-all text-ink-3">{run.repo_full_name.split('/')[0]}</p>
            )}
            <h1 className="text-2xl font-bold tracking-tight break-all">
              {run.repo_full_name?.split('/')[1] ?? '(リポジトリ不明)'}
              {run.pr_number != null && <span className="text-ink-3"> #{run.pr_number}</span>}
            </h1>
            <p className="mt-1 font-mono text-xs text-ink-3">
              head <span title={run.head_sha}>{shortSha(run.head_sha)}</span>
              {' ← '}
              base <span title={run.base_sha ?? undefined}>{shortSha(run.base_sha)}</span>
            </p>
          </div>
          {run.gate_status ? (
            <GateBadge status={run.gate_status} />
          ) : (
            <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">
              未確定（{EXEC_STATE_LABELS[run.exec_state]}）
            </span>
          )}
        </div>

        <p className="text-sm text-ink-2">{gateReason}</p>
        {semanticDiff && <p className="text-xs text-ink-3">{semanticDiff}</p>}

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <CountTile verdict="VIOLATED" count={counts.VIOLATED} />
          <CountTile verdict="REVIEW" count={counts.REVIEW} />
          <CountTile verdict="UNKNOWN" count={counts.UNKNOWN} />
          <CountTile verdict="VERIFIED_IN_SCOPE" count={counts.VERIFIED_IN_SCOPE} />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          {canLinkGithub ? (
            <>
              <a className="text-primary underline-offset-4 hover:underline" href={githubPrUrl(run.repo_full_name!, run.pr_number!)} target="_blank" rel="noreferrer">
                GitHub の PR
              </a>
              <a className="text-primary underline-offset-4 hover:underline" href={githubChecksUrl(run.repo_full_name!, run.pr_number!)} target="_blank" rel="noreferrer">
                Check
              </a>
            </>
          ) : (
            <span className="text-ink-3">GitHub へのリンクなし（PR 情報が未設定）</span>
          )}
          <div className="ml-auto">
            <RerunButton runId={run.id} onStarted={(res) => navigate(paths.run(res.run_id), { state: { rerun: res } })} />
          </div>
        </div>

        {rerun && rerun.run_id === run.id && (
          <p className="rounded-md bg-muted/70 px-3 py-2 text-xs text-ink-2">
            再実行を開始しました（本日 {rerun.used_today}/{rerun.limit} 回、残り {rerun.remaining}）
            {!rerun.cost_ok && ` ・ 費用枠超過のためエージェント調査は行われません（${rerun.cost_note}）`}
          </p>
        )}

        <p className="text-xs text-ink-3">
          作成 {formatDateTime(run.created_at)} ・ 完了 {formatDateTime(run.finished_at)} ・ トリガー {run.trigger}
        </p>
      </Panel>

      <ScopeBlock title="保証範囲" scopeItems={scopeItems} assumptions={assumptions} unresolvedItems={unresolvedItems} />
    </div>
  )
}

function CountTile({ verdict, count }: { verdict: Verdict; count: number }) {
  return (
    <div className="row flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
      <span className="text-2xl font-bold tabular-nums">{count}</span>
      <VerdictBadge verdict={verdict} />
    </div>
  )
}

/**
 * 確定できる REVIEW に、この run で効いた確定（自己確定の印つき）を出し、owner / approver には
 * ボタンを出す（S70）。確定の後は再検査の run へ移る。
 */
function FindingAttest({ slug, run, finding, mayAttest }: FindingAttestProps) {
  const navigate = useNavigate()
  const target = attestationTarget(finding)
  if (!target) return null
  const recorded = recordedAttestation(target, run.manifest?.attestations)
  return (
    <div className="space-y-2">
      {recorded && (
        <p className="text-sm text-ink-2">
          {describeAttestation(recorded)}（{recorded.actor}）
        </p>
      )}
      {recorded && configChangeWarning(recorded) && (
        <p role="alert" className="text-sm font-semibold text-bad">
          {configChangeWarning(recorded)}
        </p>
      )}
      {requiresPrComment(target) ? (
        <p className="text-sm text-ink-2">
          対応表・免除・規約の変更の確定は PR コメントで行います（PR の作成者と照合するため）:{' '}
          <code className="font-mono text-xs break-all">{prCommentCommand(target)}</code>
        </p>
      ) : (
        mayAttest &&
        slug && (
          <AttestButton
            slug={slug}
            runId={run.id}
            target={target}
            onDone={(recheck) => {
              if (recheck) navigate(paths.run(recheck))
            }}
          />
        )
      )}
    </div>
  )
}

function FindingsByVerdict({ run, supplements }: { run: ReviewRunDetail; supplements: AgentSupplement[] }) {
  const { me } = useMe()
  const slug = run.workspace ?? null
  const role = slug ? me?.workspaces.find((w) => w.slug === slug)?.role : null
  const mayAttest = slug !== null && canAttest(role)
  if (run.findings.length === 0) {
    return <p className="text-sm text-ink-3">この検査にはまだ所見がありません（実行中、または未完了の可能性があります）。</p>
  }
  const grouped = groupFindingsByVerdict(run.findings)
  return (
    <div className="space-y-6">
      {DETAIL_VERDICTS.map((verdict) => {
        const items = grouped[verdict]
        if (items.length === 0) return null
        return (
          <div key={verdict} className="space-y-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink-2">
              <VerdictBadge verdict={verdict} />
              {items.length} 件
            </h3>
            {items.map((finding) => (
              <div key={finding.id} className="space-y-2">
                <FindingCard finding={finding} repoFullName={run.repo_full_name} headSha={run.head_sha} />
                {verdict === 'REVIEW' && (
                  <SupplementList supplements={supplements.filter((s) => s.requirement_id === finding.requirement_id)} />
                )}
                <FindingAttest slug={slug} run={run} finding={finding} mayAttest={mayAttest} />
              </div>
            ))}
          </div>
        )
      })}

      {grouped.VERIFIED_IN_SCOPE.length > 0 && (
        <details className="space-y-3">
          <summary className="cursor-pointer text-sm font-semibold text-ink-2">
            {VERDICT_LABELS.VERIFIED_IN_SCOPE} {grouped.VERIFIED_IN_SCOPE.length} 件（開いて表示）
          </summary>
          <div className="mt-3 space-y-3">
            {grouped.VERIFIED_IN_SCOPE.map((finding) => (
              <FindingCard key={finding.id} finding={finding} repoFullName={run.repo_full_name} headSha={run.head_sha} />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
