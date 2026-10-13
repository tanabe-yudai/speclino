/** ランディングページ（S58）の文言と見本データ。画面の部品には文言を埋め込まない。 */

import type { LandingStep, Principle, SampleCheck } from '@/types/landing'
import type { NextActionKind } from '@/types/project'

export const HERO = {
  eyebrow: 'SPEC × CODE × AGENT',
  titleLead: '仕様はいつもの場所に。',
  titleAccent: '検証はエージェントと。',
  body: '承認した仕様と実装の「意味の差」を、検査器が反例つきで示します。テストが通っても残る違反を、PR の中で見つけます。',
  cta: '使い方を見る',
}

export const SAMPLE_PR: { label: string; checks: SampleCheck[] } = {
  label: 'PR #42 · 注文キャンセル',
  checks: [
    { id: 'R-CANCEL-01', title: '未発送ならキャンセルできる', verdict: 'VERIFIED_IN_SCOPE' },
    {
      id: 'R-CANCEL-04',
      title: '発送済みは在庫を変えない',
      verdict: 'VIOLATED',
      counterexample: '反例: status=shipped, user.is_admin=false → stock +1',
    },
    { id: 'R-CANCEL-05', title: '返金は 1 回だけ', verdict: 'UNKNOWN' },
  ],
}

export const STEPS: LandingStep[] = [
  {
    number: '01',
    title: 'リポジトリをつなぐ',
    body: 'GitHub App をインストールし、ワークスペースに登録します。登録する人がそのリポジトリの管理者であることを、GitHub で確かめてから登録します。',
  },
  {
    number: '02',
    title: '仕様を書いて、PR で承認',
    body: '仕様は Markdown のまま Git に置きます。承認は PR のコメント 1 行。承認した内容はハッシュで記録され、変われば承認し直すまで通りません。',
  },
  {
    number: '03',
    title: 'エディタから使う',
    body: 'ワークスペースの MCP トークンで、エージェントが仕様の語彙と規約を読んで実装し、その場で検査器にかけます。判定するのは検査器です。',
  },
  {
    number: '04',
    title: 'PR で確かめる',
    body: '検査器が反例つきで判定します。確認できなかったものは通しません。プロジェクトの画面で、全体の状態と次にやることが分かります。',
  },
]

export const SAMPLE_REPO = {
  workspace: 'example-org',
  repositories: [{ name: 'example-org/orders', branch: 'main' }],
  inputPlaceholder: 'owner/name',
  action: 'GitHub で確認して追加',
}

export const SAMPLE_SPEC = {
  path: 'specs/cancel_order.md',
  lines: ['## 発送済みの場合', '', 'キャンセルを受け付けず、在庫数を変更しない。'],
  comment: '/speclino approve-spec sha256:3f9a…c21e',
}

export const SAMPLE_MCP = {
  command: 'claude mcp add --transport http speclino https://<your-speclino>/mcp --header "Authorization: Bearer slmcp_…"',
  responses: ['context_get → 語彙 9 · 規約 5 · 要求 23', 'verification_start → 検査を開始', 'verification_get → 違反 1 · 未検証 1'],
}

export const SAMPLE_PROJECT: {
  name: string
  counts: { violated: number; review: number; unknown: number; exempted: number; ok: number }
  next: { kind: NextActionKind; id: string; hint: string }[]
} = {
  name: 'orders',
  counts: { violated: 1, review: 1, unknown: 1, exempted: 1, ok: 24 },
  next: [
    { kind: 'violated', id: 'R-CANCEL-04', hint: '反例を見る' },
    { kind: 'review', id: 'R-REFUND-02', hint: '人の確認が要る質問に答える' },
    { kind: 'unknown', id: 'R-CANCEL-05', hint: 'エージェントの調査の結果を見る' },
  ],
}

export const PRINCIPLES: Principle[] = [
  { title: '判定は 4 値のまま', body: '適合 / 違反 / 未検証 / 要確認。分からないものを適合と言いません。' },
  { title: 'AI は判定しない', body: '判定は検査器が行います。エージェントは証拠集めと人への質問だけを担います。' },
  { title: '記録が残る', body: '仕様も規約も承認の履歴つき。過去の判定は同じ入力から再現できます。' },
]

export const INVITE_ONLY = 'SpecLino は現在、招待制で提供しています。'

export const REPO_URL = 'https://github.com/tanabe-yudai/spec-lino'

export const OPEN_SOURCE = 'ソースコードは GitHub で公開しています（AGPL-3.0）。'

export const NAV = { start: 'はじめ方', principles: '原則', login: 'ログイン', github: 'GitHub', startHeading: 'はじめ方', principlesHeading: '原則' }
