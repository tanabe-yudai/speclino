/** ランディングページ（S58）の文言と見本データの型。 */

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

export interface Principle {
  title: string
  body: string
}

/** `/` を開いたときの振る舞い。wait = 認証の確認中（何も描かない）。 */
export type LandingDecision = 'wait' | 'app' | 'landing'
