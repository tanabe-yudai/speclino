import { Link } from 'react-router-dom'
import { Panel } from '@/components/app/Panel'
import { KNOWLEDGE_KIND_LABELS } from '@/services/labels'
import { artifactStates, policyStates } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import type { ArtifactRowData, PolicyRowData } from '@/types/navigation'
import { KnowledgeStateBadges } from './KnowledgeStateBadges'

const ROW = 'row flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm'

function KindChip({ label }: { label: string }) {
  return <span className="w-14 shrink-0 text-xs text-ink-3">{label}</span>
}

/**
 * 知識の行（種類・名前・スコープ・状態）。規約の行（policy）は先頭に 1 行。
 * from はエディタから戻る先のプロジェクト（owner/repo）。projectNames は project ID → 表示名。
 */
export function KnowledgeRows({
  slug,
  rows,
  policy,
  from,
  projectNames,
}: {
  slug: string
  rows: ArtifactRowData[]
  policy: PolicyRowData | null
  from: string | null
  projectNames: Map<string, string>
}) {
  if (rows.length === 0 && !policy) return <Panel className="text-sm text-ink-2">条件に合う知識はありません。</Panel>
  return (
    <ul className="space-y-2" aria-label="知識の一覧">
      {policy && (
        <li className={ROW}>
          <KindChip label={KNOWLEDGE_KIND_LABELS.policy} />
          <Link to={paths.policies(slug)} className="min-w-[10rem] flex-1 truncate font-medium text-primary underline-offset-4 hover:underline">
            チームの規約（ルール {policy.rules}）
          </Link>
          <span className="rounded-full bg-secondary px-2.5 py-1 text-xs whitespace-nowrap text-ink-2">チーム共通</span>
          <KnowledgeStateBadges
            states={policyStates(policy)}
            revisions={{ active: policy.activeRevision, pending: policy.draftRevision }}
          />
        </li>
      )}
      {rows.map(({ artifact: a, workspaceWide }) => (
        <li key={a.artifact_id} className={ROW}>
          <KindChip label={KNOWLEDGE_KIND_LABELS[a.kind]} />
          <Link
            to={paths.knowledgeItem(slug, a.kind, a.artifact_id, { from })}
            className="min-w-[10rem] flex-1 truncate font-mono font-medium text-primary underline-offset-4 hover:underline"
          >
            {a.name}
          </Link>
          <span className="max-w-[14rem] truncate rounded-full bg-secondary px-2.5 py-1 text-xs whitespace-nowrap text-ink-2">
            {workspaceWide ? 'チーム共通' : (a.scope?.projects.map((p) => projectNames.get(p) ?? p).join(', ') ?? '—')}
          </span>
          <KnowledgeStateBadges states={artifactStates(a)} revisions={{ active: a.active_revision, retired: a.retired_revision, pending: a.draft_revision }} />
          <Link to={paths.knowledgeRevisions(slug, a.kind, a.artifact_id, from)} className="text-xs text-primary underline-offset-4 hover:underline">
            改訂と承認
          </Link>
        </li>
      ))}
    </ul>
  )
}
