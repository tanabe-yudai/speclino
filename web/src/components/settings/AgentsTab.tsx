import { Panel, PanelTitle } from '@/components/app/Panel'
import { A2aPanel } from '@/components/settings/A2aPanel'
import { CopyBlock } from '@/components/settings/CopyBlock'
import { SyncTokensTab } from '@/components/settings/SyncTokensTab'
import { TokensTab } from '@/components/settings/TokensTab'
import { mcpEnvLines, mcpUrl, syncCommand } from '@/services/agentConnection'
import { AGENT_CONNECTION_LABEL, REPO_SYNC_LABEL } from '@/services/labels'
import type { WorkspaceRole } from '@/types/me'

/**
 * エージェント接続: 各自のエージェントへ承認済みの知識を届ける 2 つの経路。
 * MCP / A2A（エージェント接続トークン。発行はオーナーだけ）と、リポジトリ同期（speclino sync。
 * リポジトリ同期トークンの一覧は全員、発行はオーナーと承認者）。手順とトークンを並べる。
 */
export function AgentsTab({ slug, role }: { slug: string; role: WorkspaceRole | null }) {
  const origin = window.location.origin
  return (
    <div className="space-y-10">
      <section className="space-y-4" aria-labelledby="agents-mcp">
        <h2 id="agents-mcp" className="text-base font-semibold">
          {AGENT_CONNECTION_LABEL}
        </h2>
        <Panel className="space-y-3">
          <PanelTitle>MCP で接続する</PanelTitle>
          <p className="text-sm text-ink-2">
            エディタのエージェントが、規約・仕様・スキルを読み（context_get / spec_get / skill_get）、検査を始められます。接続先は次の URL で、
            <code className="mx-1 font-mono text-xs">Authorization: Bearer</code>にエージェント接続トークンを渡します。
          </p>
          <CopyBlock label="MCP の接続先" text={mcpUrl(origin)} />
          <p className="text-sm text-ink-2">Claude Code のプラグイン（plugins/speclino）では、接続先とトークンを環境変数で渡します（設定ファイルに書かない）。</p>
          <CopyBlock label="MCP の環境変数" text={mcpEnvLines(origin)} />
        </Panel>
        <A2aPanel origin={origin} />
        {role === 'owner' ? (
          <div className="space-y-4">
            <h3 className="text-sm font-semibold">エージェント接続トークン</h3>
            <TokensTab slug={slug} />
          </div>
        ) : (
          <p className="text-sm text-ink-3">エージェント接続トークンはオーナーが発行します。</p>
        )}
      </section>
      <section className="space-y-4" aria-labelledby="agents-sync">
        <h2 id="agents-sync" className="text-base font-semibold">
          {REPO_SYNC_LABEL}
        </h2>
        <Panel className="space-y-3">
          <PanelTitle>リポジトリに書き出す</PanelTitle>
          <p className="text-sm text-ink-2">
            承認済みのスキル・指示書・仕様をリポジトリに書き出し、.speclino/lock.json を更新します。lock をコミットすると、ゲートが鮮度を確かめます（KNOWLEDGE-LOCK-001）。
          </p>
          <CopyBlock label="リポジトリ同期の手順" text={syncCommand(origin)} />
        </Panel>
        <SyncTokensTab slug={slug} canManage={role === 'owner' || role === 'approver'} />
      </section>
    </div>
  )
}
