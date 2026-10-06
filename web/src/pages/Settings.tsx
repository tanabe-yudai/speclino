import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { MembersTab } from '@/components/settings/MembersTab'
import { ProjectsTab } from '@/components/settings/ProjectsTab'
import { RepositoriesTab } from '@/components/settings/RepositoriesTab'
import { SyncTokensTab } from '@/components/settings/SyncTokensTab'
import { TokensTab } from '@/components/settings/TokensTab'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import { pickSettingsTab, visibleSettingsTabs } from '@/services/settingsTabs'
import type { SettingsTab } from '@/types/command'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

const TAB_LABELS: Record<SettingsTab, string> = {
  projects: 'プロジェクト',
  repositories: 'リポジトリ',
  members: 'メンバー',
  sync: 'sync トークン',
  tokens: 'MCP トークン',
}

/** ワークスペースの設定（プロジェクト・リポジトリ・メンバー・sync トークン・MCP トークン）。MCP トークンは owner にだけ出す。 */
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
  const visible = visibleSettingsTabs(membership.role)
  const tab = pickSettingsTab(params.get('tab'), visible)

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Settings</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight break-words">{membership.name ?? slug} の設定</h1>
      </header>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList>
            {visible.map((t) => (
              <TabsTrigger key={t} value={t}>
                {TAB_LABELS[t]}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <TabsContent value="projects" className="mt-6">
          <ProjectsTab slug={slug} role={membership.role} />
        </TabsContent>
        <TabsContent value="repositories" className="mt-6">
          <RepositoriesTab slug={slug} isOwner={membership.role === 'owner'} />
        </TabsContent>
        <TabsContent value="members" className="mt-6">
          <MembersTab slug={slug} role={membership.role} myUid={me.uid} onSelfChanged={reload} />
        </TabsContent>
        {visible.includes('sync') && (
          <TabsContent value="sync" className="mt-6">
            <SyncTokensTab slug={slug} canManage={membership.role === 'owner' || membership.role === 'approver'} />
          </TabsContent>
        )}
        {visible.includes('tokens') && (
          <TabsContent value="tokens" className="mt-6">
            <TokensTab slug={slug} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
