import { Link } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { Button } from '@/components/ui/button'
import { paths } from '@/services/paths'
import type { VerdictTone } from '@/types/components'
import type { NextAction, NextActionKind, ProjectRun } from '@/types/project'

const KIND: Record<NextActionKind, { label: string; tone: VerdictTone }> = {
  violated: { label: '違反', tone: 'bad' },
  stale: { label: '承認切れ', tone: 'rev' },
  review: { label: '要確認', tone: 'rev' },
  unknown: { label: '未検証', tone: 'unk' },
  unapproved: { label: '未承認', tone: 'rev' },
}

/** 次にやること（design 5.3）。並びは services/nextActions.ts の規則。 */
export function NextActionsCard({ actions, run }: { actions: NextAction[]; run: ProjectRun }) {
  return (
    <Panel className="space-y-3">
      <PanelTitle>次にやること</PanelTitle>
      {actions.length === 0 ? (
        <p className="text-sm text-ink-2">すべて確認済みです。</p>
      ) : (
        <ol className="space-y-2">
          {actions.map((a) => (
            <li key={a.requirementId}>
              <Link to={paths.run(run.id)} className="row block min-w-0 px-3 py-2">
                <span className="flex min-w-0 items-center gap-2">
                  <ToneBadge tone={KIND[a.kind].tone}>{KIND[a.kind].label}</ToneBadge>
                  <code className="truncate font-mono text-xs">{a.requirementId}</code>
                </span>
                {a.title && <span className="mt-1 block truncate text-sm">{a.title}</span>}
                <span className="mt-0.5 block text-xs text-ink-3">{a.hint}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}
      <Button variant="secondary" size="sm" className="w-full" disabled title="準備中">
        この件について質問する
      </Button>
    </Panel>
  )
}
