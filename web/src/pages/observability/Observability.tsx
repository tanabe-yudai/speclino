import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { getObservability } from '@/api/observability'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { LoadingState } from '@/components/LoadingState'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { formatDateTime, shortSha } from '@/services/format'
import { GATE_STATUS_LABELS, POLICY_LEVEL_LABELS } from '@/services/labels'
import {
  CHANNEL_LABELS,
  RULE_TIER_LABELS,
  UNMEASURED,
  fetchLabel,
  fetchLag,
  formatRatio,
  lagLabel,
  lagTone,
  percent,
  unknownEntries,
} from '@/services/observability'
import { paths } from '@/services/paths'
import type { FetchHealth, LlmStability, ProjectHealth, RulePoint, RuleTrend, TrendRun, WorkspaceHealth } from '@/types/observability'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

const th = 'py-1.5 pr-3 font-medium whitespace-nowrap'
const td = 'py-2 pr-3 align-top'

/** 可観測性（設計 9 章）。知識が使われ、守られているか。表示はサーバの集計 1 つだけから作る。 */
export function Observability() {
  const workspace = useCurrentWorkspace()
  useDocumentTitle('利用状況')
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  return <ObservabilityBody key={workspace.slug} slug={workspace.slug} name={workspace.name ?? workspace.slug} />
}

/** embedded: ワークスペースのタブの中に置くとき（見出しはタブ側にあるので出さない）。 */
export function ObservabilityBody({ slug, name, embedded = false }: { slug: string; name: string; embedded?: boolean }) {
  const [health, setHealth] = useState<WorkspaceHealth | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    getObservability(slug)
      .then((h) => alive && setHealth(h))
      .catch((err: unknown) => alive && setError(err instanceof ApiError ? err.message : '可観測性の集計を取得できませんでした'))
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    setError(null)
    setHealth(null)
    setAttempt((n) => n + 1)
  }, [])

  return (
    <div className="space-y-6">
      <header className="min-w-0">
        {!embedded && (
          <>
            <p className="text-xs font-semibold tracking-widest text-primary uppercase">Observability</p>
            <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{name} の利用状況</h1>
          </>
        )}
        <p className={embedded ? 'text-sm text-ink-2' : 'mt-1 text-sm text-ink-2'}>
          知識が使われ、守られているか。測っていないものは「{UNMEASURED}」と表示し、0% や 100% にはしません。
        </p>
      </header>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && !health && <LoadingState message="可観測性の集計を取得しています…" />}
      {health && (
        <>
          <p className="text-sm text-ink-2">
            {health.current_generation !== null
              ? `今の bundle: 世代 ${health.current_generation}`
              : 'まだ bundle はありません'}
            <span className="text-ink-3">（集計 {formatDateTime(health.generated_at)}）</span>
          </p>
          <ProjectsPanel slug={slug} projects={health.projects} />
          <FetchesPanel fetches={health.fetches} days={health.fetch_window_days} />
          <RulesPanel runs={health.trend_runs} rules={health.rules} />
          <LlmPanel llm={health.llm} />
        </>
      )}
    </div>
  )
}

function ProjectsPanel({ slug, projects }: { slug: string; projects: ProjectHealth[] }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>リポジトリ</PanelTitle>
      <p className="text-xs text-ink-3">
        形式化率は今の bundle の仕様の要求の表、対応率と UNKNOWN の内訳は最新の完了した検査から。
      </p>
      {projects.length === 0 ? (
        <p className="text-sm text-ink-2">登録されたリポジトリはありません。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-3">
              <tr>
                <th scope="col" className={th}>リポジトリ</th>
                <th scope="col" className={th}>形式化率</th>
                <th scope="col" className={th}>
                  対応率<span className="block font-normal">（UNKNOWN の理由の分類にもとづく推定）</span>
                </th>
                <th scope="col" className={th}>UNKNOWN の内訳</th>
                <th scope="col" className={th}>最新の検査</th>
                <th scope="col" className={th}>検査の bundle</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.project_id} className="border-t">
                  <td className={td}>
                    <Link to={paths.project(slug, p.repo_full_name)} className="font-mono text-primary underline-offset-4 hover:underline">
                      {p.repo_full_name}
                    </Link>
                  </td>
                  <td className={`${td} whitespace-nowrap`}>{formatRatio(p.formalization)}</td>
                  <td className={`${td} whitespace-nowrap`}>{formatRatio(p.binding)}</td>
                  <td className={td}>
                    <UnknownCell project={p} />
                  </td>
                  <td className={`${td} whitespace-nowrap`}>
                    {p.latest_run ? (
                      <Link to={paths.run(p.latest_run.run_id)} className="text-primary underline-offset-4 hover:underline">
                        {p.latest_run.gate_status ? GATE_STATUS_LABELS[p.latest_run.gate_status] : '判定なし'}・
                        {shortSha(p.latest_run.head_sha)}
                      </Link>
                    ) : (
                      <span className="text-ink-3">{UNMEASURED}</span>
                    )}
                  </td>
                  <td className={`${td} whitespace-nowrap`}>
                    {p.latest_run?.bundle_generation != null ? (
                      <span className="inline-flex items-center gap-2">
                        世代 {p.latest_run.bundle_generation}
                        <ToneBadge tone={lagTone(p.latest_run.generation_lag)}>{lagLabel(p.latest_run.generation_lag)}</ToneBadge>
                      </span>
                    ) : (
                      <span className="text-ink-3">{p.latest_run ? 'bundle 外' : UNMEASURED}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function UnknownCell({ project }: { project: ProjectHealth }) {
  if (!project.unknown_breakdown) return <span className="text-ink-3">{UNMEASURED}</span>
  const entries = unknownEntries(project.unknown_breakdown)
  if (entries.length === 0) return <span className="text-ink-3">なし</span>
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map((e) => (
        <ToneBadge key={e.reason} tone="unk">
          {e.label} {e.count}
        </ToneBadge>
      ))}
    </span>
  )
}

function FetchesPanel({ fetches, days }: { fetches: FetchHealth[]; days: number }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>知識の取得（直近 {days} 日）</PanelTitle>
      <p className="text-xs text-ink-3">sync トークン・MCP・メンバーごとに、最後に取得した bundle と今の bundle との差。</p>
      {fetches.length === 0 ? (
        <p className="text-sm text-ink-2">期間内の取得も、発行中の sync トークンもありません。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-3">
              <tr>
                <th scope="col" className={th}>取得元</th>
                <th scope="col" className={th}>経路</th>
                <th scope="col" className={th}>リポジトリ</th>
                <th scope="col" className={th}>最後の取得</th>
                <th scope="col" className={th}>遅れ</th>
              </tr>
            </thead>
            <tbody>
              {fetches.map((f) => (
                <tr key={`${f.channel}:${f.token_id ?? ''}:${f.uid ?? ''}:${f.project_id ?? ''}`} className="border-t">
                  <td className={`${td} font-mono`}>{fetchLabel(f)}</td>
                  <td className={td}>{CHANNEL_LABELS[f.channel] ?? f.channel}</td>
                  <td className={`${td} font-mono`}>{f.repo_full_name ?? '-'}</td>
                  <td className={`${td} whitespace-nowrap`}>
                    {f.last_fetched_at ? (
                      `${formatDateTime(f.last_fetched_at)}（世代 ${f.last_generation}）`
                    ) : (
                      <span className="text-ink-3">期間内の取得なし</span>
                    )}
                  </td>
                  <td className={td}>
                    <ToneBadge tone={fetchLag(f).tone}>{fetchLag(f).label}</ToneBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const BAR_SEGMENTS: { key: keyof RulePoint; className: string; label: string }[] = [
  { key: 'violated', className: 'bg-bad', label: 'VIOLATED' },
  { key: 'review', className: 'bg-rev', label: 'REVIEW' },
  { key: 'unknown', className: 'bg-unk', label: 'UNKNOWN' },
  { key: 'verified', className: 'bg-ok', label: 'VERIFIED' },
]

function TrendBar({ point, run }: { point: RulePoint | null; run: TrendRun }) {
  const when = `${run.repo_full_name}・${formatDateTime(run.created_at)}`
  if (!point) {
    return (
      <span title={`${when}: 結果なし`} className="flex h-6 w-2.5 items-end">
        <span className="h-1 w-full rounded-sm bg-muted" />
        <span className="sr-only">{when}: 結果なし</span>
      </span>
    )
  }
  const total = point.violated + point.review + point.unknown + point.verified
  const text = BAR_SEGMENTS.filter((s) => point[s.key] > 0)
    .map((s) => `${s.label} ${point[s.key]}`)
    .join('、')
  return (
    <span title={`${when}: ${text}`} className="flex h-6 w-2.5 flex-col-reverse overflow-hidden rounded-sm bg-muted">
      {BAR_SEGMENTS.map((s) =>
        point[s.key] > 0 ? <span key={s.key} className={s.className} style={{ height: `${(point[s.key] / total) * 100}%` }} /> : null,
      )}
      <span className="sr-only">
        {when}: {text}
      </span>
    </span>
  )
}

function RulesPanel({ runs, rules }: { runs: TrendRun[]; rules: RuleTrend[] }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>規約ごとの推移</PanelTitle>
      <p className="text-xs text-ink-3">
        ワークスペース全体の直近 {runs.length} 回の完了した検査（最大 10 回、左が古い）。棒の色は VIOLATED・REVIEW・UNKNOWN・VERIFIED の割合、灰色の短い線は結果なし。
      </p>
      {rules.length === 0 ? (
        <p className="text-sm text-ink-2">規約も規約の検査結果もありません。</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-3">
              <tr>
                <th scope="col" className={th}>規約</th>
                <th scope="col" className={th}>種類</th>
                <th scope="col" className={th}>推移</th>
                <th scope="col" className={th}>直近</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => {
                const last = [...r.points].reverse().find((p) => p !== null) ?? null
                return (
                  <tr key={r.rule_id} className="border-t">
                    <td className={td}>
                      <span className="font-mono">{r.rule_id}</span>
                      {r.title && <span className="block text-xs text-ink-3">{r.title}</span>}
                    </td>
                    <td className={`${td} whitespace-nowrap text-xs`}>
                      {r.level ? POLICY_LEVEL_LABELS[r.level] : '-'}・{r.tier ? RULE_TIER_LABELS[r.tier] : '今の改訂に無い'}
                    </td>
                    <td className={td}>
                      <span className="flex items-end gap-1">
                        {r.points.map((p, i) => (
                          <TrendBar key={runs[i].run_id} point={p} run={runs[i]} />
                        ))}
                      </span>
                    </td>
                    <td className={`${td} whitespace-nowrap text-xs`}>
                      {last ? (
                        <span className="flex flex-wrap gap-1">
                          {last.violated > 0 && <ToneBadge tone="bad">VIOLATED {last.violated}</ToneBadge>}
                          {last.review > 0 && <ToneBadge tone="rev">REVIEW {last.review}</ToneBadge>}
                          {last.unknown > 0 && <ToneBadge tone="unk">UNKNOWN {last.unknown}</ToneBadge>}
                          {last.verified > 0 && <ToneBadge tone="ok">VERIFIED {last.verified}</ToneBadge>}
                        </span>
                      ) : (
                        <span className="text-ink-3">{UNMEASURED}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function LlmPanel({ llm }: { llm: LlmStability | null }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>LLM 支援の安定度</PanelTitle>
      {!llm ? (
        <p className="text-sm text-ink-2">{UNMEASURED}（LLM 支援の規約の検査結果がまだありません）</p>
      ) : (
        <>
        <p className="text-xs text-ink-3">
          指摘・split の件数とキャッシュの当たり率はワークスペース全体の直近 10 回の完了した検査から（当たり＝検査の作成（受付）より前から評価の記録があった）。人の確定は今の件数。必須の規約は「指摘なし」も人が確定するまでゲートを止める。
        </p>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Stat label="指摘あり" value={String(llm.issue)} />
          <Stat label="指摘なし（機械検証ではない）" value={String(llm.no_issue)} />
          <Stat label="割れた項目（split）" value={String(llm.split)} />
          <Stat label="キャッシュの当たり率" value={percent(llm.cache_hit_rate)} />
          <Stat label="人の確定" value={count(llm.attestations)} />
          <Stat label="うち PR の作成者の自己確定" value={count(llm.self_attested)} />
          <Stat label="うち自己確定かどうか照合できない" value={count(llm.attestations_unverified)} />
        </dl>
        </>
      )}
    </Panel>
  )
}

function count(n: number | null): string {
  return n === null ? UNMEASURED : String(n)
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-0.5 font-semibold">{value}</dd>
    </div>
  )
}
