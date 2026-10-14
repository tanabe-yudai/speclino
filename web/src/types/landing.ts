/** ランディングページの文言と見本データの型。 */

import type { Verdict } from './finding'

/** PR の検査の見本 1 行。verdict は確定後に出す判定。 */
export interface SampleCheck {
  id: string
  title: string
  verdict: Verdict
  /** 違反の見本だけに付ける反例の一行 */
  counterexample?: string
}

export interface LandingStep {
  number: '01' | '02' | '03' | '04'
  title: string
  body: string
}

/** エージェントの仕組みの図に置く 1 役。icon は Landing 側で対応づける。 */
export interface AgentRole {
  key: 'coding' | 'writer' | 'investigator' | 'a2a'
  name: string
  /** どこから働くか（MCP・画面・PR・A2A） */
  via: string
  points: string[]
  /** 代表的なツール名。無ければ空 */
  tools: string[]
}

/** エージェントの見本のセッションの 1 行。kind で色を分ける。 */
export interface SessionLine {
  kind: 'user' | 'tool' | 'result' | 'wait'
  text: string
}

export interface TrustItem {
  key: 'fail-closed' | 'ai-proposes' | 'approved-only' | 'traceable'
  title: string
  body: string
}

/** `/` を開いたときの振る舞い。wait = 認証の確認中（何も描かない）。 */
export type LandingDecision = 'wait' | 'app' | 'landing'
