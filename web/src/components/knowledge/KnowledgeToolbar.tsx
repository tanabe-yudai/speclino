import { Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { TabNav } from '@/components/shell/TabNav'
import { Button } from '@/components/ui/button'
import { KNOWLEDGE_KIND_LABELS, KNOWLEDGE_STATE_LABELS } from '@/services/labels'
import { KNOWLEDGE_STATUS_FILTERS } from '@/services/knowledgeNav'
import type { ArtifactKind } from '@/types/knowledge'
import type { KnowledgeStatusFilter } from '@/types/navigation'

const ALL = 'all'

/** 種類ごとの画面の上の帯: 状態の絞り込み（URL のクエリ）・追加の絞り込み（children）・「+ AI と作る」。 */
export function KnowledgeToolbar({
  kind,
  status,
  statusPath,
  createPath,
  children,
}: {
  kind: ArtifactKind
  status: KnowledgeStatusFilter | null
  statusPath: (status: KnowledgeStatusFilter | null) => string
  createPath: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <TabNav
        label="状態で絞る"
        current={status ?? ALL}
        items={[
          { id: ALL, label: 'すべての状態', to: statusPath(null) },
          ...KNOWLEDGE_STATUS_FILTERS.map((s) => ({ id: s, label: KNOWLEDGE_STATE_LABELS[s], to: statusPath(s) })),
        ]}
      />
      {children}
      <Button asChild size="sm" className="ml-auto bg-brand text-white">
        <Link to={createPath}>
          <Plus className="size-4" /> AI と{KNOWLEDGE_KIND_LABELS[kind]}を作る
        </Link>
      </Button>
    </div>
  )
}
