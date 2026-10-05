/** A2A（他のエージェントから相談する窓口）の接続情報（S60）。コンポーネントには埋め込まない。 */

export const AGENT_CARD_PATH = '/.well-known/agent-card.json'

/** Web と同じオリジンで配信される Agent Card の URL。 */
export function agentCardUrl(origin: string): string {
  return `${origin.replace(/\/+$/, '')}${AGENT_CARD_PATH}`
}
