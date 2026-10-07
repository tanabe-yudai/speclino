import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { MembersTab } from '@/components/settings/MembersTab'
import { IntegrationsTab } from '@/components/settings/IntegrationsTab'
import { ProjectSettingsTab } from '@/components/settings/ProjectSettingsTab'
import { RepositoriesTab } from '@/components/settings/RepositoriesTab'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import { SETTINGS_TABS, pickSettingsTab } from '@/services/settingsTabs'
import type { SettingsTab } from '@/types/command'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

const TAB_LABELS: Record<SettingsTab, string> = {
  members: 'メンバー',
  repositories: 'リポジトリ',
  integrations: '連携',
  project: 'プロジェクト',
}

/** ワークスペースの設定。連携の中のエージェント接続トークンはオーナーにだけ出す。 */
export function Settings() {
  const { slug } = useParams<{ slug: string }>()
  const { me, reload } = useMe()
  const [params, setParams] = useSearchParams()
  useDocumentTitle('設定')
  if (!slug) return <ErrorState message="ワークスペースが指定されていません" />
  if (!me) return <LoadingState />
  const membership = me.workspaces.find((w) => w.slug === slug) ?? null
  if (!membership) {
    return (
      <Panel className="mx-auto max-w-xl text-center">
        <PanelTitle className="text-base">このワークスペースは見つかりません</PanelTitle>
        <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
          ホームへ戻る
        </Link>
      </Panel>
    )
  }
  const tab = pickSettingsTab(params.get('tab'))

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Settings</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight break-words">{membership.name ?? slug} の設定</h1>
      </header>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList>
            {SETTINGS_TABS.map((t) => (
              <TabsTrigger key={t} value={t}>
                {TAB_LABELS[t]}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <TabsContent value="members" className="mt-6">
          <MembersTab slug={slug} role={membership.role} myUid={me.uid} onSelfChanged={reload} />
        </TabsContent>
        <TabsContent value="repositories" className="mt-6">
          <RepositoriesTab slug={slug} isOwner={membership.role === 'owner'} />
        </TabsContent>
        <TabsContent value="integrations" className="mt-6">
          <IntegrationsTab slug={slug} role={membership.role} />
        </TabsContent>
        <TabsContent value="project" className="mt-6">
          <ProjectSettingsTab slug={slug} role={membership.role} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
