/** 設定の「エージェント接続」に出す接続先と手順の文字列（MCP・リポジトリ同期）。A2A は a2a.ts。 */

export const MCP_PATH = '/mcp'

const trim = (origin: string) => origin.replace(/\/+$/, '')

/** MCP の接続先（Web と同じオリジン）。 */
export function mcpUrl(origin: string): string {
  return `${trim(origin)}${MCP_PATH}`
}

/** Claude Code プラグイン（plugins/speclino）へ接続先とトークンを渡す環境変数。トークンは書かない。 */
export function mcpEnvLines(origin: string): string {
  return [`export SPECLINO_MCP_URL=${mcpUrl(origin)}`, 'export SPECLINO_MCP_TOKEN=slmcp_...'].join('\n')
}

/** リポジトリ同期（speclino sync）の手順。トークンは環境変数だけで渡す。 */
export function syncCommand(origin: string): string {
  return [
    'export SPECLINO_SYNC_TOKEN=slsync_...',
    `uv run speclino sync --url ${trim(origin)} --repo . --repo-id <GitHub のリポジトリ ID>`,
  ].join('\n')
}
