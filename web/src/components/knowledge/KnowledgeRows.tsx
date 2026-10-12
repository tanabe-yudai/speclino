import { Link } from 'react-router-dom'
import { Panel } from '@/components/app/Panel'
import { KNOWLEDGE_KIND_LABELS } from '@/services/labels'
import { artifactStates } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import type { ArtifactSummary } from '@/types/knowledge'
import { KnowledgeStateBadges } from './KnowledgeStateBadges'

const ROW = 'row flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm'

/**
 * 知識の行（名前・スコープ・状態・改訂と承認）。showKind は種類の混ざる一覧（取り下げ済みの知識）で種類を添える。
 * from はエディタから戻る先のプロジェクト（owner/repo）。projectNames は project ID → 表示名。
 */
export function KnowledgeRows({
  slug,
  artifacts,
  from,
  projectNames,
  showKind = false,
  empty = '条件に合う知識はありません。',
  label,
}: {
  slug: string
  artifacts: ArtifactSummary[]
  from: string | null
  projectNames: Map<string, string>
  showKind?: boolean
  empty?: string
  label: string
}) {
  if (artifacts.length === 0) return <Panel className="text-sm text-ink-2">{empty}</Panel>
  return (
    <ul className="space-y-2" aria-label={label}>
      {artifacts.map((a) => (
        <li key={a.artifact_id} className={ROW}>
          {showKind && <span className="w-14 shrink-0 text-xs text-ink-3">{KNOWLEDGE_KIND_LABELS[a.kind]}</span>}
          <Link
            to={paths.knowledgeItem(slug, a.kind, a.artifact_id, { from })}
            className="min-w-[10rem] flex-1 truncate font-mono font-medium text-primary underline-offset-4 hover:underline"
          >
            {a.name}
          </Link>
          <span className="max-w-[14rem] truncate rounded-full bg-secondary px-2.5 py-1 text-xs whitespace-nowrap text-ink-2">
            {a.scope?.workspace ? 'チーム共通' : (a.scope?.projects.map((p) => projectNames.get(p) ?? p).join(', ') ?? '—')}
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
