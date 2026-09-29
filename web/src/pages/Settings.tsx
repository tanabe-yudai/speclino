import { useParams, useSearchParams } from 'react-router-dom'
import { RepositoriesTab } from '@/components/settings/RepositoriesTab'
import { TokensTab } from '@/components/settings/TokensTab'
import { ErrorState } from '@/components/ErrorState'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMe } from '@/hooks/useMe'
import { pickSettingsTab, visibleSettingsTabs } from '@/services/settingsTabs'
import type { SettingsTab } from '@/types/command'

const TAB_LABELS: Record<SettingsTab, string> = { repositories: 'リポジトリ', tokens: 'MCP トークン' }

/** ワークスペースの設定（リポジトリ・MCP トークン）。MCP トークンは owner にだけ出す。 */
export function Settings() {
  const { slug } = useParams<{ slug: string }>()
  const { me } = useMe()
  const [params, setParams] = useSearchParams()
  if (!slug) return <ErrorState message="ワークスペースが指定されていません" />
  const membership = me?.workspaces.find((w) => w.slug === slug) ?? null
  const visible = visibleSettingsTabs(membership?.role ?? null)
  const tab = pickSettingsTab(params.get('tab'), visible)

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold tracking-widest text-primary uppercase">Settings</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">{membership?.name ?? slug} の設定</h1>
      </header>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <TabsList>
          {visible.map((t) => (
            <TabsTrigger key={t} value={t}>
              {TAB_LABELS[t]}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="repositories" className="mt-6">
          <RepositoriesTab slug={slug} isOwner={membership?.role === 'owner'} />
        </TabsContent>
        {visible.includes('tokens') && (
          <TabsContent value="tokens" className="mt-6">
            <TokensTab slug={slug} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
