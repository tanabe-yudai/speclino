/** `GET /api/review-runs/{id}/supplements` の1件。正式な回答ではない（エージェントの補足）。 */
export interface AgentSupplement {
  id: string
  requirement_id: string | null
  question: string
  content: string
  created_at: string
  /** どのエージェント（トークン名）から来たか。 */
  source: string
}

export interface SupplementsResponse {
  supplements: AgentSupplement[]
}
