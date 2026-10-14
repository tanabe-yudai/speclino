/** ランディングページの文言と見本データ。画面の部品には文言を埋め込まない。 */

import type { AgentRole, LandingStep, SampleCheck, SessionLine, TrustItem } from '@/types/landing'

export const HERO = {
  eyebrow: 'KNOWLEDGE × AGENTS',
  titleLead: 'チームの知識を、',
  titleAccent: 'すべてのエージェントへ。',
  body: '規約・スキル・指示書・仕様を 1 か所で書いて承認し、MCP で各自のコーディングエージェントに配ります。エージェントは知識を読むだけでなく、仕様を書いて下書きに出すこともできます。PR では、承認済みの仕様と実装の食い違いを反例つきで示します。',
  cta: '使い方を見る',
}

/** ヒーローの見本: 利用者のコーディングエージェントが MCP で仕様を書き、検査まで回す流れ。 */
export const SAMPLE_SESSION: { label: string; lines: SessionLine[] } = {
  label: 'あなたのエディタ · coding agent',
  lines: [
    { kind: 'user', text: '注文キャンセルの仕様に「発送済みは在庫を変えない」を足して' },
    { kind: 'tool', text: 'spec_get spec-cancel-001' },
    { kind: 'result', text: '改訂 3 · 要求 4 · 図 2' },
    { kind: 'tool', text: 'spec_draft_save spec-cancel-001' },
    { kind: 'result', text: '下書き 改訂 4 · 図の診断 0 件' },
    { kind: 'wait', text: '承認待ち — 承認するのはチームの人です' },
    { kind: 'tool', text: 'verification_start' },
    { kind: 'result', text: '違反 1 · 反例 status=shipped → stock +1' },
  ],
}

export const AGENTS_SECTION = {
  eyebrow: 'AGENTS',
  heading: 'エージェントの仕組み',
  lead: 'あなたのコーディングエージェントと、SpecLino の中で Gemini と Agent Development Kit（ADK）で動くエージェントが、承認済みの知識を中心に働きます。',
  hubTitle: 'SpecLino',
  hubPoints: ['承認済みの知識（bundle）', '決定的な検査器'],
  hubNote: '判定を出すのは検査器だけ',
  human: { title: 'チームの人', body: '知識の承認と、要確認の確定をする' },
}

export const AGENT_ROLES: AgentRole[] = [
  {
    key: 'coding',
    name: 'あなたのコーディングエージェント',
    via: 'MCP',
    points: ['承認済みの規約・スキル・仕様を取り込む', '仕様を書いて下書きに出す（図の診断つき）', '書いたコードをその場で検査にかける'],
    tools: ['knowledge_pull', 'spec_draft_save', 'verification_start'],
  },
  {
    key: 'writer',
    name: '執筆エージェント',
    via: '画面の「AI と作る」',
    points: ['選択肢つきの質問で要件を詰める', '承認済みの知識・リポジトリ・Google 検索で調べる', '書き換えを差分で提案する（採るかは人）'],
    tools: [],
  },
  {
    key: 'investigator',
    name: '調査エージェント',
    via: 'PR の検査のあと',
    points: ['未検証が残ったときだけ動く', '再解析・コード検索・テストの隔離実行で証拠を集める', '分からないことは承認者に質問する'],
    tools: [],
  },
  {
    key: 'a2a',
    name: 'ほかのエージェント',
    via: 'A2A',
    points: ['承認済みの知識を読む', '知識の書き換えを提案する'],
    tools: [],
  },
]

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
    title: '知識を書いて、画面で承認',
    body: '規約・スキル・指示書・仕様を、画面の「AI と作る」か、エディタのエージェントから MCP で書きます。承認すると番号つきの bundle になり、配信も検査も同じ番号を読みます。',
  },
  {
    number: '03',
    title: 'エージェントに配る',
    body: 'エディタに MCP を登録すると、エージェントが承認済みの知識を取り込み、AGENTS.md・スキル・仕様の写しを書き出します。エージェントの無い環境では speclino sync で同じものを書き出します。',
  },
  {
    number: '04',
    title: 'PR で確かめる',
    body: '承認済みの仕様と実装を検査器が照合し、違反は反例つきで示します。確認できなかったものは通さず、調査エージェントが証拠を集めます。',
  },
]

export const SAMPLE_REPO = {
  workspace: 'example-org',
  repositories: [{ name: 'example-org/orders', branch: 'main' }],
  inputPlaceholder: 'owner/name',
  action: 'GitHub で確認して追加',
}

export const SAMPLE_APPROVE = {
  name: 'spec-cancel-001',
  kind: '仕様',
  revision: '改訂 4 · 下書き',
  author: '作成: エディタのエージェント（MCP）',
  diff: [
    { sign: '+', text: '| R-CANCEL-04 | mandatory | 発送済みはキャンセルを受け付けず、在庫を変えない |' },
    { sign: ' ', text: '| R-CANCEL-05 | mandatory | 返金は 1 回だけ |' },
  ],
  diagnostics: '図の診断 0 件',
  action: '承認して有効にする',
  done: '改訂 4 を承認しました。以後の検査に使われます',
}

export const SAMPLE_MCP = {
  command: 'claude mcp add --transport http speclino https://<your-speclino>/mcp --header "Authorization: Bearer slmcp_…"',
  responses: ['knowledge_pull → bundle 41 · 書き出し 11 件', 'AGENTS.md · .claude/skills/ · .speclino/specs/', 'lock.json を更新（署名つき）'],
}

export const TRUST = {
  heading: '安心して任せられる理由',
  lead: 'エージェントに書かせても、チームが承認していないものは効かず、確かめられないものは通りません。',
}

export const TRUST_ITEMS: TrustItem[] = [
  {
    key: 'fail-closed',
    title: '分からないものを「適合」にしない',
    body: '判定は 適合 / 違反 / 未検証 / 要確認 の 4 つ。解析できない部分は未検証にし、必須の要求に未検証や要確認が残れば PR は通りません。',
  },
  {
    key: 'ai-proposes',
    title: 'AI は書いて、調べるだけ',
    body: 'どのエージェントにも、判定を書くツールを持たせていません。判定は決定的な検査器が出し、AI の提案を採るかどうかは人が決めます。',
  },
  {
    key: 'approved-only',
    title: '承認したものだけが届く',
    body: '承認のたびに署名つきの bundle を作り、配信も PR の検査も同じ番号を読みます。手元の写しが古かったり書き換えられていたりすれば、lock の照合で止まります。',
  },
  {
    key: 'traceable',
    title: 'あとから辿れる',
    body: '検査は bundle とコミットで固定し、同じ入力から再現できます。どのエージェントがいつ知識を取り込んだかも記録し、観測の画面で見られます。',
  },
]

export const INVITE_ONLY = 'SpecLino は現在、招待制で提供しています。'

export const REPO_URL = 'https://github.com/tanabe-yudai/speclino'

export const OPEN_SOURCE = 'ソースコードは GitHub で公開しています（AGPL-3.0）。'

export const NAV = {
  agents: 'エージェント',
  start: 'はじめ方',
  trust: '任せられる理由',
  login: 'ログイン',
  github: 'GitHub',
}
