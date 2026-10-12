import { PageHeader } from '@/components/app/PageHeader'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { RunBrowser } from '@/components/runs/RunBrowser'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { SECTION_LABELS } from '@/services/labels'

/** 検査（`/w/:slug/runs?gate=&project=`）: PR ごとの検査とゲートの判定。 */
export function Runs() {
  const workspace = useCurrentWorkspace()
  useDocumentTitle(SECTION_LABELS.runs)
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Checks" title={SECTION_LABELS.runs}>
        PR ごとに、承認済みの仕様と規約でコードを検査した結果です。ゲートは検査の判定（通過・失敗）です。
      </PageHeader>
      <RunBrowser key={workspace.slug} slug={workspace.slug} />
    </div>
  )
}
