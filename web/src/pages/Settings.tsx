import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { AgentsTab } from '@/components/settings/AgentsTab'
import { MembersTab } from '@/components/settings/MembersTab'
import { ProjectSettingsTab } from '@/components/settings/ProjectSettingsTab'
import { RepositoriesTab } from '@/components/settings/RepositoriesTab'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMe } from '@/hooks/useMe'
import { SETTINGS_TAB_LABELS } from '@/services/labels'
import { legacyRedirect } from '@/services/legacyRoutes'
import { paths } from '@/services/paths'
import { SETTINGS_TABS, parseSettingsTab } from '@/services/settingsTabs'
import type { SettingsTab } from '@/types/command'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** ワークスペースの設定（`/w/:slug/settings/:tab`）。エージェント接続の中のトークンの発行は権限で出し分ける。 */
export function Settings() {
  const { slug, tab: rawTab } = useParams<{ slug: string; tab?: string }>()
  const { pathname, search } = useLocation()
  const navigate = useNavigate()
  const { me, reload } = useMe()
  useDocumentTitle('設定')
  if (!slug) return <ErrorState message="ワークスペースが指定されていません" />
  // 旧い ?tab= と旧いタブ名は新しいタブのパスへ
  const legacy = legacyRedirect(pathname, search)
  const tab = parseSettingsTab(rawTab)
  if (legacy || tab === null) return <Navigate to={legacy ?? paths.settings(slug)} replace />
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

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Settings</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight break-words">{membership.name ?? slug} の設定</h1>
      </header>
      <Tabs value={tab} onValueChange={(v) => navigate(paths.settings(slug, v as SettingsTab), { replace: true })}>
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList>
            {SETTINGS_TABS.map((t) => (
              <TabsTrigger key={t} value={t}>
                {SETTINGS_TAB_LABELS[t]}
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
        <TabsContent value="agents" className="mt-6">
          <AgentsTab slug={slug} role={membership.role} />
        </TabsContent>
        <TabsContent value="projects" className="mt-6">
          <ProjectSettingsTab slug={slug} role={membership.role} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
