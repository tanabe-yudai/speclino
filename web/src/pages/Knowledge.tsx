import { PageHeader } from '@/components/app/PageHeader'
import { KnowledgeBrowser } from '@/components/knowledge/KnowledgeBrowser'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { SECTION_LABELS } from '@/services/labels'

/** 知識（`/w/:slug/knowledge?kind=&project=&status=`）: 仕様・スキル・指示書・規約を 1 つの一覧で。 */
export function Knowledge() {
  const workspace = useCurrentWorkspace()
  useDocumentTitle(SECTION_LABELS.knowledge)
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Knowledge" title={SECTION_LABELS.knowledge}>
        チームがエージェントに渡す仕様・スキル・指示書・規約。改訂して承認すると、各自のエージェントに配られます。
      </PageHeader>
      <KnowledgeBrowser key={workspace.slug} slug={workspace.slug} />
    </div>
  )
}
