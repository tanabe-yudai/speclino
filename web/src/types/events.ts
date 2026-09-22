/**
 * `GET /api/review-runs/{id}/events` の1件。type は既知の集合を想定するが、
 * 将来の追加に備えて string も許容する（Timeline は未知の type もラベル表示できる）。
 */

export type RunEventType =
  | 'state'
  | 'agent.start'
  | 'plan'
  | 'tool.policy_check'
  | 'tool.analyze_focus'
  | 'tool.repo_search'
  | 'audit'
  | 'agent.end'

export interface RunEvent {
  seq: number
  ts: string
  type: RunEventType | (string & {})
  payload: Record<string, unknown>
}
