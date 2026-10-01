import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { Button } from '@/components/ui/button'
import { POLICY_LEVEL_LABELS } from '@/services/labels'
import { diffPackages } from '@/services/policyDraft'
import type { PoliciesView, PolicyPackage, PolicyRule } from '@/types/policy'

/**
 * ルールの一覧（design 6 章の中央）。下書きがあれば下書きの中身を出し、有効な改訂から変わった行に印を付ける。
 * 直近の成否はワークスペースの各プロジェクトの最新の検査から（結果が無ければ「結果なし」）。
 */
export function RuleList({
  view,
  pkg,
  onEdit,
}: {
  view: PoliciesView
  pkg: PolicyPackage
  onEdit: (rule: PolicyRule | null) => void
}) {
  const changes = view.draft ? diffPackages(view.active?.package ?? null, pkg) : []
  const kindOf = new Map(changes.map((c) => [c.ruleId, c.kind]))
  const removed = (view.active?.package.rules ?? []).filter((r) => kindOf.get(r.id) === 'removed')

  return (
    <Panel className="space-y-3">
      <PanelTitle>ルール</PanelTitle>
      {pkg.rules.length === 0 && <p className="text-sm text-ink-3">ルールはまだありません。</p>}
      <ul className="divide-y overflow-hidden rounded-lg border">
        {pkg.rules.map((r) => {
          const result = view.results[r.id]
          const kind = kindOf.get(r.id)
          return (
            <li key={r.id} className="space-y-1.5 px-4 py-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <code className="min-w-0 font-mono text-xs break-all">{r.id}</code>
                <span className="min-w-0 flex-1 text-sm font-medium break-words">{r.title || '（題なし）'}</span>
                {kind && <ToneBadge tone="rev">{kind === 'added' ? '追加' : '変更'}</ToneBadge>}
                <Button variant="secondary" size="sm" onClick={() => onEdit(r)}>
                  編集
                </Button>
              </div>
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
                <span>{POLICY_LEVEL_LABELS[r.level]}</span>
                <span>{r.verifier ? `検査器 ${r.verifier.id}` : '助言のみ（検査器なし）'}</span>
                <span>{r.exceptions_allowed ? '例外を認める' : '例外を認めない'}</span>
                {kind ? (
                  <span className="text-ink-3">未検査（下書きの変更。承認後の検査から）</span>
                ) : result?.status ? (
                  <span className="flex items-center gap-1.5">
                    <VerdictBadge verdict={result.status} />
                    <span>
                      {result.failing_projects}/{result.projects} プロジェクトで未達
                    </span>
                  </span>
                ) : (
                  <span className="text-ink-3">結果なし</span>
                )}
              </p>
              <p className="font-mono text-[11px] break-all text-ink-3">{r.applies_to.paths.join(', ') || '（適用範囲なし）'}</p>
            </li>
          )
        })}
        {removed.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-x-3 px-4 py-3 text-ink-3 line-through">
            <code className="font-mono text-xs break-all">{r.id}</code>
            <span className="text-sm">{r.title}</span>
            <ToneBadge tone="rev">削除</ToneBadge>
          </li>
        ))}
      </ul>
      <Button variant="secondary" onClick={() => onEdit(null)}>
        ルールを追加
      </Button>
    </Panel>
  )
}
