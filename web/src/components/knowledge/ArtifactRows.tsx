import { Link } from 'react-router-dom'
import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { KIND_LABELS } from '@/services/knowledge'
import { paths } from '@/services/paths'
import type { ArtifactKind } from '@/types/knowledge'
import type { ArtifactRowData } from '@/types/navigation'

/** 仕様・スキル・指示書の行（状態・有効な改訂・取り下げ済み・下書き）。from はエディタから戻る先のプロジェクト。 */
export function ArtifactRows({ slug, kind, rows, from }: { slug: string; kind: ArtifactKind; rows: ArtifactRowData[]; from?: string | null }) {
  const label = KIND_LABELS[kind]
  if (rows.length === 0) return <Panel className="text-sm text-ink-2">まだ{label}はありません。</Panel>
  return (
    <ul className="space-y-2" aria-label={`${label}の一覧`}>
      {rows.map(({ artifact: a, workspaceWide }) => (
        <li key={a.artifact_id} className="row flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
          <Link
            to={paths.knowledgeItem(slug, kind, a.artifact_id, { from })}
            className="min-w-0 flex-1 truncate font-mono font-medium text-primary underline-offset-4 hover:underline"
          >
            {a.name}
          </Link>
          {workspaceWide && from && <span className="rounded-full bg-secondary px-2.5 py-1 text-xs whitespace-nowrap text-ink-2">チーム共通</span>}
          {a.active_revision !== null ? (
            <ToneBadge tone="ok">有効 改訂 {a.active_revision}</ToneBadge>
          ) : a.retired_revision != null ? (
            <span className="rounded-full bg-muted px-2.5 py-1 text-xs whitespace-nowrap text-ink-3">取り下げ済み 改訂 {a.retired_revision}</span>
          ) : (
            <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-ink-3">未承認</span>
          )}
          {a.draft_revision !== null && <ToneBadge tone="rev">下書き 改訂 {a.draft_revision}</ToneBadge>}
          <Link to={paths.knowledgeRevisions(slug, kind, a.artifact_id, from)} className="text-xs text-primary underline-offset-4 hover:underline">
            改訂と承認
          </Link>
        </li>
      ))}
    </ul>
  )
}
