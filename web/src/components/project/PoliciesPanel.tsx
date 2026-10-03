import { Panel, PanelTitle } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { POLICY_LEVEL_LABELS } from '@/services/labels'
import type { ProjectOverview } from '@/types/project'

function verifierLabel(v: boolean | null): string {
  if (v === null) return '—'
  return v ? '検査器あり' : '検査器なし'
}

/** 適用されている規約: チームの改訂とプロジェクト固有の規約（design 5.1-3）。分からない値は「—」。 */
export function PoliciesPanel({ overview }: { overview: ProjectOverview }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>適用されている規約</PanelTitle>
      <p className="text-sm text-ink-2">
        {overview.policy_team
          ? `チーム規約 ${overview.policy_team} 改訂 ${overview.policy_revision ?? '—'}`
          : 'チーム規約は適用されていません'}
      </p>
      {overview.policies.length === 0 ? (
        <p className="text-sm text-ink-3">適用された規約はありません。</p>
      ) : (
        <ul className="divide-y overflow-hidden rounded-lg border">
          {overview.policies.map((p) => (
            <li key={p.rule_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
              <code className="min-w-0 flex-1 font-mono text-xs break-all">{p.rule_id}</code>
              <span className="text-xs text-ink-3">{p.origin === 'team' ? 'チーム' : 'プロジェクト'}</span>
              <span className="text-xs text-ink-3">{p.level ? POLICY_LEVEL_LABELS[p.level] : '—'}</span>
              <span className="text-xs text-ink-3">{verifierLabel(p.has_verifier)}</span>
              {p.status ? (
                <VerdictBadge verdict={p.status} />
              ) : (
                <span className="text-xs text-ink-3">この検査では結果なし</span>
              )}
              <span className="w-full text-sm text-ink-2">{p.title ?? '—'}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
