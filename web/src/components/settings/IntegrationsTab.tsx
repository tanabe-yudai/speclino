import { SyncTokensTab } from '@/components/settings/SyncTokensTab'
import { TokensTab } from '@/components/settings/TokensTab'
import type { WorkspaceRole } from '@/types/me'

/** 連携: リポジトリ同期トークン（全員が一覧、発行はオーナーと承認者）とエージェント接続トークン（オーナーだけ）。 */
export function IntegrationsTab({ slug, role }: { slug: string; role: WorkspaceRole | null }) {
  return (
    <div className="space-y-10">
      <section className="space-y-4">
        <h2 className="text-base font-semibold">リポジトリ同期トークン</h2>
        <SyncTokensTab slug={slug} canManage={role === 'owner' || role === 'approver'} />
      </section>
      {role === 'owner' && (
        <section className="space-y-4">
          <h2 className="text-base font-semibold">エージェント接続トークン</h2>
          <TokensTab slug={slug} />
        </section>
      )}
    </div>
  )
}
