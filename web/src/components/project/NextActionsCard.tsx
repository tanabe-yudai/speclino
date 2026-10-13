import { Link } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { plainTerms } from '@/services/displayText'
import { NEXT_ACTION_KIND, systemRuleTitle } from '@/services/labels'
import { paths } from '@/services/paths'
import type { NextAction, ProjectRun } from '@/types/project'


/** 次にやること。並びは services/nextActions.ts の規則。 */
export function NextActionsCard({ slug, actions, run }: { slug: string; actions: NextAction[]; run: ProjectRun }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>次にやること</PanelTitle>
      {actions.length === 0 ? (
        <p className="text-sm text-ink-2">すべて確認済みです。</p>
      ) : (
        <ol className="space-y-2">
          {actions.map((a) => (
            <li key={a.requirementId}>
              <Link to={paths.run(slug, run.id)} className="row block min-w-0 px-3 py-2">
                <span className="flex min-w-0 items-center gap-2">
                  {/* システム規約は平易な題が状況を言うので、「承認切れ」などの仕様向けの区分は出さない */}
                  {!systemRuleTitle(a.requirementId) && (
                    <ToneBadge tone={NEXT_ACTION_KIND[a.kind].tone}>{NEXT_ACTION_KIND[a.kind].label}</ToneBadge>
                  )}
                  <span className="truncate text-sm">{a.title ? plainTerms(a.title) : a.requirementId}</span>
                </span>
                {a.title && <code className="mt-0.5 block truncate font-mono text-xs text-ink-3">{a.requirementId}</code>}
                <span className="mt-0.5 block text-xs text-ink-3">{plainTerms(a.hint)}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
