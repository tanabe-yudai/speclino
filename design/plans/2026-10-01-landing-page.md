# ランディングページ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 未ログインの人に「何ができて、なぜ新しいか」と使い始め方を伝えるランディングページを `/` に置く（Issue #78 S58）。

**Architecture:** 文言と見本データは `web/src/services/landingContent.ts` に集め（画面に文言を散らさず、禁止語をテストで検査できるようにする）、判定が順に確定するアニメーションの規則は純粋関数（`services/reveal.ts`）にする。見本は画像ではなく、新しい UI の部品（`HealthRing`・`VerdictBadge`・`ToneBadge`・`Panel`）を見本データで描く静的な部品（`components/landing/`）で作り、リンクや API 呼び出しを持たせない。`/` はログイン済みなら `/app` へ送る。

**Tech Stack:** React 19 / Vite 8 / Tailwind v4 / shadcn/ui、vitest。

**Spec:** `design/2026-09-30-ui-redesign.md` 7 章（1 章の目的と条件、3 章のトークン）。ワイヤーフレーム: `.superpowers/brainstorm/2345034-1790692175/content/landing-v2.html`（方向性は承認済み。実装で質感を上げる）。

## Global Constraints

- ログインの入口はヘッダーの 1 か所だけ。末尾は「SpecLino は現在、招待制で提供しています。」でボタンを置かない（新規登録の入口は置かない）
- 「デモ」という言葉を使わない（サービスとして書く）。個人の環境の値（GitHub のアカウント名・GCP の識別子・本番 URL・本名・メール）を書かない。例は `example-org/...`
- 見本は実データではない。判定の 4 値（適合 / 違反 / 未検証 / 要確認）と「AI は判定しない」を崩す表現をしない
- 見本の中身は実際の製品の形に合わせる: 承認コメントは `/speclino approve-spec sha256:<要求の内容ハッシュ>`（要求 ID ではない。README 278 行）、MCP のツールは `context_get` / `verification_start` / `verification_get`
- 質感は「光の空間」（明るい地に柔らかな光のグラデーション、浮いたパネル）。オーロラ系の強い発光は使わない。色・影は `styles/globals.css` の既存のもの（`text-brand`（グラデーションの文字）・`shadow-soft`・`shadow-lift`・`text-ok` / `bg-bad-bg` など）だけを使う。例外は MCP のコマンドの見本（`SampleMcp`）だけで、端末らしく見せるためにライト / ダークどちらでも暗い固定の色（`#131c33` の地に `#e8ecf8` の文字）にする
- 動きを減らす設定（`prefers-reduced-motion: reduce`）ではアニメーションせず、最終の状態を出す
- スマホ幅（390px）で横スクロールを出さない。ライト / ダークの両方で読める
- ページのタイトルと OGP（`og:title`・`og:description`・`og:type`・`twitter:card`）を設定する。`og:url` と `og:image` は置かない（URL は環境ごとに違い、個人の環境の値を書かないため。画像は作らない）

## Review Focus

1. ログイン済みの人が `/` を開く → ランディングページが一瞬も見えずに `/app` へ。認証の確認中はランディングページを描かない
2. 390px 幅 → ヘッダー（ロゴ・テーマ切り替え・ログイン）が 1 行に収まり、コマンドの見本（`claude mcp add ...`）が横にはみ出さない（見本の中で折り返すか、見本の中だけ横スクロール）
3. 動きを減らす設定 → 見本の判定が最初から全部出ている
4. 見本の中のボタン風の要素・入力欄を押しても何も起きない（ボタンとして読み上げられない）。見本のリンクで実在しないページへ飛ばない
5. ダークテーマ → 見出しのグラデーションの文字・コードの見本が背景に溶けない

---

## 設計上の判断（計画の時点で決めたこと）

- **見本の承認コメントを実際の形に直す**: ワイヤーフレームの `/speclino approve-spec R-CANCEL-04` は実在しない形（コマンドは要求の内容ハッシュを取る）なので `sha256:…` にする
- **見本は静的な部品**: `NextActionsCard` などの画面の部品は実在の run へのリンクを持つので使わず、`HealthRing`・`VerdictBadge`・`ToneBadge`・`Panel` だけを再利用する。見本の外枠は `aria-hidden` にし、代わりに `sr-only` の説明文を置く
- **ナビゲーションのページ内リンク**（「はじめ方」「原則」）は 640px 未満では隠す（ヘッダーを 1 行に収めるため。本文を下へ読めば届く）
- **アニメーション**は 1 回だけ（ページを開いてから 0.9 秒ごとに 1 件ずつ確定）。繰り返さない

---

### Task 1: 文言・見本データ・確定の規則

**Files:**
- Create: `web/src/types/landing.ts`
- Create: `web/src/services/landingContent.ts`、Test: `web/src/services/landingContent.test.ts`
- Create: `web/src/services/reveal.ts`、Test: `web/src/services/reveal.test.ts`
- Create: `web/src/services/landingRoute.ts`、Test: `web/src/services/landingRoute.test.ts`

**Interfaces:**
- Produces: 型 `SampleCheck`・`LandingStep`・`Principle`・`LandingDecision`。定数 `HERO`・`SAMPLE_PR`・`STEPS`・`PRINCIPLES`・`INVITE_ONLY`・`SAMPLE_REPO`・`SAMPLE_SPEC`・`SAMPLE_MCP`・`SAMPLE_PROJECT`。関数 `revealedCount(elapsedMs: number, stepMs: number, total: number, reducedMotion: boolean): number`、`landingDecision(loading: boolean, signedIn: boolean): LandingDecision`

- [ ] **Step 1: 型を書く**（`web/src/types/landing.ts`）

```ts
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
```

- [ ] **Step 2: 失敗するテストを書く**

`web/src/services/landingContent.test.ts`（文言と見本の全部の文字列を集めて検査する）:

```ts
import { describe, expect, it } from 'vitest'
import * as content from './landingContent'

function strings(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(strings)
  if (value && typeof value === 'object') return Object.values(value).flatMap(strings)
  return []
}

const all = strings(content)

describe('landingContent', () => {
  it('「デモ」と言わない', () => {
    expect(all.filter((s) => /デモ|demo/i.test(s))).toEqual([])
  })

  it('個人の環境の値を書かない', () => {
    const banned = /tanabe|example-project|6h5m3svkja|gmail\.com|run\.app/i
    expect(all.filter((s) => banned.test(s))).toEqual([])
  })

  it('はじめ方は 01〜04、原則は 3 つ', () => {
    expect(content.STEPS.map((s) => s.number)).toEqual(['01', '02', '03', '04'])
    expect(content.PRINCIPLES).toHaveLength(3)
  })

  it('承認コメントの見本は内容ハッシュを取る形', () => {
    expect(content.SAMPLE_SPEC.comment).toMatch(/^\/speclino approve-spec sha256:/)
  })

  it('見本の検査は 4 値だけを使い、違反には反例がある', () => {
    for (const c of content.SAMPLE_PR.checks) {
      expect(['VERIFIED_IN_SCOPE', 'VIOLATED', 'UNKNOWN', 'REVIEW']).toContain(c.verdict)
      if (c.verdict === 'VIOLATED') expect(c.counterexample).toBeTruthy()
    }
  })

  it('末尾は招待制の一文', () => {
    expect(content.INVITE_ONLY).toBe('SpecLino は現在、招待制で提供しています。')
  })
})
```

`web/src/services/reveal.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { revealedCount } from './reveal'

describe('revealedCount', () => {
  it('開いた直後は 0 件', () => {
    expect(revealedCount(0, 900, 3, false)).toBe(0)
  })
  it('経過時間に応じて 1 件ずつ確定する', () => {
    expect(revealedCount(899, 900, 3, false)).toBe(0)
    expect(revealedCount(900, 900, 3, false)).toBe(1)
    expect(revealedCount(2000, 900, 3, false)).toBe(2)
  })
  it('全部確定したらそのまま', () => {
    expect(revealedCount(99_000, 900, 3, false)).toBe(3)
  })
  it('動きを減らす設定では最初から全部', () => {
    expect(revealedCount(0, 900, 3, true)).toBe(3)
  })
})
```

`web/src/services/landingRoute.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { landingDecision } from './landingRoute'

describe('landingDecision', () => {
  it('認証の確認中は何も描かない（ログイン済みの人にランディングページを一瞬も見せない）', () => {
    expect(landingDecision(true, false)).toBe('wait')
    expect(landingDecision(true, true)).toBe('wait')
  })
  it('ログイン済みならアプリへ', () => {
    expect(landingDecision(false, true)).toBe('app')
  })
  it('未ログインならランディングページ', () => {
    expect(landingDecision(false, false)).toBe('landing')
  })
})
```

- [ ] **Step 3: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/landingContent.test.ts src/services/reveal.test.ts src/services/landingRoute.test.ts`
Expected: FAIL（3 ファイルとも import できない）

- [ ] **Step 4: 実装する**

`web/src/services/reveal.ts`:

```ts
/** 見本の判定を開いてから stepMs ごとに 1 件ずつ確定させる。動きを減らす設定では最初から全部。 */
export function revealedCount(elapsedMs: number, stepMs: number, total: number, reducedMotion: boolean): number {
  if (reducedMotion) return total
  if (elapsedMs <= 0) return 0
  return Math.min(total, Math.floor(elapsedMs / stepMs))
}
```

`web/src/services/landingRoute.ts`:

```ts
import type { LandingDecision } from '@/types/landing'

/** `/` を開いたとき: 認証の確認中は待つ、ログイン済みならアプリ、未ログインならランディングページ。 */
export function landingDecision(loading: boolean, signedIn: boolean): LandingDecision {
  if (loading) return 'wait'
  return signedIn ? 'app' : 'landing'
}
```

`web/src/services/landingContent.ts`（文言はここだけに置く。見本は架空の `example-org/orders`）:

```ts
/** ランディングページ（S58、design 7 章）の文言と見本データ。画面の部品には文言を埋め込まない。 */

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
    body: 'ワークスペースの MCP トークンで、エージェントが仕様の語彙と規約を読み、実装してその場で検証します。',
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
  counts: { VERIFIED_IN_SCOPE: number; VIOLATED: number; UNKNOWN: number; REVIEW: number }
  unanalyzed: number
  next: { kind: NextActionKind; id: string; hint: string }[]
} = {
  name: 'orders',
  counts: { VERIFIED_IN_SCOPE: 20, VIOLATED: 1, UNKNOWN: 1, REVIEW: 1 },
  unanalyzed: 0,
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

export const NAV = { start: 'はじめ方', principles: '原則', login: 'ログイン', startHeading: 'はじめ方', principlesHeading: '原則' }
```

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npx oxlint`
Expected: PASS（新しい 13 件を含む全件）

- [ ] **Step 6: コミット**

```bash
git add web/src/types/landing.ts web/src/services/landingContent.ts web/src/services/landingContent.test.ts web/src/services/reveal.ts web/src/services/reveal.test.ts web/src/services/landingRoute.ts web/src/services/landingRoute.test.ts
git commit -m "feat(web): ランディングページの文言・見本・確定の規則 (#78)"
```

---

### Task 2: 見本の部品

**Files:**
- Create: `web/src/hooks/usePrefersReducedMotion.ts`
- Create: `web/src/hooks/useReveal.ts`
- Create: `web/src/components/landing/Sample.tsx`（見本の外枠）
- Create: `web/src/components/landing/SamplePrCheck.tsx`
- Create: `web/src/components/landing/SampleRepo.tsx`、`SampleSpec.tsx`、`SampleMcp.tsx`、`SampleProject.tsx`

**Interfaces:**
- Consumes: Task 1 の `SAMPLE_*`・`revealedCount`、既存の `HealthRing`（`components/project/HealthRing.tsx`）・`healthRatio`（`services/health.ts`）・`VerdictBadge`・`ToneBadge`・`Panel`
- Produces: `usePrefersReducedMotion(): boolean`、`useReveal(total: number, stepMs: number): number`、`Sample({ label, children, className })`、`SamplePrCheck()`、`SampleRepo()`、`SampleSpec()`、`SampleMcp()`、`SampleProject()`

- [ ] **Step 1: フックを書く**

`usePrefersReducedMotion.ts`（`matchMedia` が無い環境でも落ちない）:

```ts
import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

function current(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(QUERY).matches
}

/** OS の「視差効果を減らす」設定。変わったら追従する。 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(current)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(QUERY)
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}
```

`useReveal.ts`:

```ts
import { useEffect, useState } from 'react'
import { revealedCount } from '@/services/reveal'
import { usePrefersReducedMotion } from './usePrefersReducedMotion'

/** マウントしてからの経過で、確定した件数を返す（services/reveal.ts の規則）。 */
export function useReveal(total: number, stepMs: number): number {
  const reduced = usePrefersReducedMotion()
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (reduced) return
    const start = performance.now()
    const id = window.setInterval(() => {
      const ms = performance.now() - start
      setElapsed(ms)
      if (ms >= total * stepMs) window.clearInterval(id)
    }, 100)
    return () => window.clearInterval(id)
  }, [reduced, total, stepMs])
  return revealedCount(elapsed, stepMs, total, reduced)
}
```

- [ ] **Step 2: 見本の部品を書く**

`Sample.tsx`（見本は読み上げでは説明文 1 つにし、中の要素はボタンやリンクとして扱わせない）:

```tsx
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 画面の見本の外枠。中身は飾りなので aria-hidden にし、label を読み上げ用に置く。 */
export function Sample({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <figure className={cn('min-w-0', className)}>
      <figcaption className="sr-only">{label}</figcaption>
      <div aria-hidden="true" className="pointer-events-none min-w-0 select-none">
        {children}
      </div>
    </figure>
  )
}
```

`SamplePrCheck.tsx`（確定前は「検査中…」を点滅、確定したら判定のバッジ。違反は反例の一行）:

```tsx
import { Panel } from '@/components/app/Panel'
import { VerdictBadge } from '@/components/app/VerdictBadge'
import { useReveal } from '@/hooks/useReveal'
import { cn } from '@/lib/utils'
import { SAMPLE_PR } from '@/services/landingContent'
import { Sample } from './Sample'

export function SamplePrCheck() {
  const shown = useReveal(SAMPLE_PR.checks.length, 900)
  return (
    <Sample label="PR の検査結果の見本: 要求ごとに適合・違反・未検証が確定していく">
      <Panel className="space-y-2 p-4 shadow-lift">
        <p className="text-xs text-ink-3">{SAMPLE_PR.label}</p>
        {SAMPLE_PR.checks.map((c, i) => {
          const done = i < shown
          const bad = done && c.verdict === 'VIOLATED'
          return (
            <div
              key={c.id}
              className={cn('rounded-lg bg-card px-3 py-2 transition-colors', bad && 'bg-bad-bg')}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <code className="font-mono text-xs">{c.id}</code>
                <span className="min-w-0 flex-1 truncate text-sm">{c.title}</span>
                {done ? (
                  <VerdictBadge verdict={c.verdict} />
                ) : (
                  <span className="text-xs text-ink-3 motion-safe:animate-pulse">検査中…</span>
                )}
              </div>
              {bad && c.counterexample && (
                <p className="mt-1 font-mono text-[11px] break-all text-ink-2">{c.counterexample}</p>
              )}
            </div>
          )
        })}
      </Panel>
    </Sample>
  )
}
```

`SampleRepo.tsx`:

```tsx
import { Panel } from '@/components/app/Panel'
import { SAMPLE_REPO } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleRepo() {
  return (
    <Sample label="リポジトリの登録画面の見本">
      <Panel className="space-y-2 p-4">
        <p className="text-xs text-ink-3">リポジトリ（{SAMPLE_REPO.workspace}）</p>
        {SAMPLE_REPO.repositories.map((r) => (
          <div key={r.name} className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg bg-secondary px-3 py-2 text-sm">
            <span className="min-w-0 truncate font-mono text-xs">{r.name}</span>
            <span className="text-xs text-ink-3">既定ブランチ {r.branch}</span>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <span className="min-w-0 flex-1 rounded-lg border bg-card px-3 py-2 text-sm text-ink-3">{SAMPLE_REPO.inputPlaceholder}</span>
          <span className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">{SAMPLE_REPO.action}</span>
        </div>
      </Panel>
    </Sample>
  )
}
```

`SampleSpec.tsx`:

```tsx
import { Panel } from '@/components/app/Panel'
import { SAMPLE_SPEC } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleSpec() {
  return (
    <Sample label="仕様の Markdown と、PR の承認コメントの見本">
      <Panel className="space-y-3 p-4 font-mono text-xs leading-relaxed">
        <p className="text-ink-3">{SAMPLE_SPEC.path}</p>
        <div>
          {SAMPLE_SPEC.lines.map((l, i) => (
            <p key={i} className="min-h-[1em] break-words">{l}</p>
          ))}
        </div>
        <p className="inline-block max-w-full rounded-lg bg-accent px-3 py-1.5 break-all text-primary">💬 {SAMPLE_SPEC.comment}</p>
      </Panel>
    </Sample>
  )
}
```

`SampleMcp.tsx`（ダークでもライトでも暗い端末の見た目。コマンドは折り返す）:

```tsx
import { SAMPLE_MCP } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleMcp() {
  return (
    <Sample label="エディタに SpecLino の MCP を追加するコマンドと、ツールの応答の見本">
      <div className="rounded-2xl bg-[#131c33] p-4 font-mono text-xs leading-relaxed text-[#e8ecf8] shadow-lift">
        <p className="break-all">$ {SAMPLE_MCP.command}</p>
        {SAMPLE_MCP.responses.map((r, i) => (
          <p key={r} className={i === SAMPLE_MCP.responses.length - 1 ? 'mt-1 text-[#4fd1a8]' : 'mt-1 text-[#9aa6c4]'}>
            › {r}
          </p>
        ))}
      </div>
    </Sample>
  )
}
```

`SampleProject.tsx`:

```tsx
import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { HealthRing } from '@/components/project/HealthRing'
import { healthRatio } from '@/services/health'
import { SAMPLE_PROJECT } from '@/services/landingContent'
import type { VerdictTone } from '@/types/components'
import type { NextActionKind } from '@/types/project'
import { Sample } from './Sample'

const TONE: Record<NextActionKind, VerdictTone> = { violated: 'bad', stale: 'rev', review: 'rev', unknown: 'unk', unapproved: 'rev' }
const LABEL: Record<NextActionKind, string> = { violated: '違反', stale: '承認切れ', review: '要確認', unknown: '未検証', unapproved: '未承認' }

export function SampleProject() {
  const c = SAMPLE_PROJECT.counts
  return (
    <Sample label="プロジェクト画面の見本: 健康状態と次にやること">
      <Panel className="flex flex-wrap items-start gap-4 p-4">
        <HealthRing size="lg" ratio={healthRatio(c, SAMPLE_PROJECT.unanalyzed)} />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="font-semibold">{SAMPLE_PROJECT.name}</p>
          <p className="flex flex-wrap gap-x-3 text-xs">
            <span className="text-ok">適合 {c.VERIFIED_IN_SCOPE}</span>
            <span className="text-bad">違反 {c.VIOLATED}</span>
            <span className="text-unk">未検証 {c.UNKNOWN}</span>
            <span className="text-rev">要確認 {c.REVIEW}</span>
          </p>
          <p className="text-xs font-semibold text-primary">✦ 次にやること</p>
          <ul className="space-y-1">
            {SAMPLE_PROJECT.next.map((n) => (
              <li key={n.id} className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
                <ToneBadge tone={TONE[n.kind]}>{LABEL[n.kind]}</ToneBadge>
                <code className="font-mono">{n.id}</code>
                <span className="text-ink-3">{n.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      </Panel>
    </Sample>
  )
}
```

- [ ] **Step 3: 確かめる**

Run: `cd web && npm test && npx tsc -b && npx oxlint && npm run build`
Expected: PASS

- [ ] **Step 4: コミット**

```bash
git add web/src/hooks/usePrefersReducedMotion.ts web/src/hooks/useReveal.ts web/src/components/landing
git commit -m "feat(web): ランディングページの見本の部品 (#78)"
```

---

### Task 3: ページ・ルート・タイトルと OGP

**Files:**
- Create: `web/src/components/landing/LandingHeader.tsx`
- Create: `web/src/pages/Landing.tsx`
- Modify: `web/src/App.tsx`（`/` → `Landing`）
- Modify: `web/index.html`（タイトル・description・OGP）
- Test: `web/src/services/indexHtml.test.ts`

**Interfaces:**
- Consumes: Task 1 の `HERO`・`STEPS`・`PRINCIPLES`・`INVITE_ONLY`・`NAV`・`landingDecision`、Task 2 の見本の部品、既存の `useAuth`・`ThemeToggle`・`LoadingState`・`Button`
- Produces: 画面のみ

- [ ] **Step 1: 失敗するテストを書く**（`web/src/services/indexHtml.test.ts`）

```ts
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')

describe('index.html', () => {
  it('タイトルと説明と OGP がある', () => {
    expect(html).toMatch(/<title>SpecLino — [^<]+<\/title>/)
    expect(html).toMatch(/<meta name="description" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:title" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:description" content="[^"]+"/)
    expect(html).toMatch(/<meta property="og:type" content="website"/)
    expect(html).toMatch(/<meta name="twitter:card" content="summary"/)
  })

  it('og:url を置かず、「デモ」や個人の環境の値を書かない', () => {
    expect(html).not.toMatch(/og:url|og:image/)
    expect(html).not.toMatch(/デモ|tanabe|example-project|run\.app/i)
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/indexHtml.test.ts`
Expected: FAIL（タイトルが `SpecLino` だけ）

- [ ] **Step 3: index.html を直す**（`<title>SpecLino</title>` を置き換える）

```html
    <title>SpecLino — 仕様と実装の意味の差を、反例つきで</title>
    <meta name="description" content="承認した仕様と実装の「意味の差」を、検査器が反例つきで示します。テストが通っても残る違反を、PR の中で見つけます。" />
    <meta property="og:title" content="SpecLino — 仕様と実装の意味の差を、反例つきで" />
    <meta property="og:description" content="仕様はいつもの場所に。検証はエージェントと。承認した仕様と実装の意味の差を、検査器が反例つきで示します。" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="SpecLino" />
    <meta name="twitter:card" content="summary" />
```

- [ ] **Step 4: ヘッダーとページを書く**

`LandingHeader.tsx`（ログインはここだけ。640px 未満ではページ内リンクを隠す）:

```tsx
import { Link } from 'react-router-dom'
import { ThemeToggle } from '@/components/shell/ThemeToggle'
import { NAV } from '@/services/landingContent'

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
        <a href="#top" className="shrink-0 font-extrabold tracking-tight">
          <span className="text-brand">◆</span> SpecLino
        </a>
        <nav className="ml-6 hidden gap-4 text-sm text-ink-3 sm:flex">
          <a className="hover:text-foreground" href="#start">{NAV.start}</a>
          <a className="hover:text-foreground" href="#principles">{NAV.principles}</a>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <Link to="/login" className="rounded-lg bg-card px-3.5 py-1.5 text-sm font-semibold shadow-soft transition-shadow hover:shadow-lift">
            {NAV.login}
          </Link>
        </div>
      </div>
    </header>
  )
}
```

`pages/Landing.tsx`:

```tsx
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { LandingHeader } from '@/components/landing/LandingHeader'
import { SampleMcp } from '@/components/landing/SampleMcp'
import { SamplePrCheck } from '@/components/landing/SamplePrCheck'
import { SampleProject } from '@/components/landing/SampleProject'
import { SampleRepo } from '@/components/landing/SampleRepo'
import { SampleSpec } from '@/components/landing/SampleSpec'
import { HERO, INVITE_ONLY, NAV, PRINCIPLES, STEPS } from '@/services/landingContent'
import { landingDecision } from '@/services/landingRoute'

const STEP_SAMPLES = [SampleRepo, SampleSpec, SampleMcp, SampleProject]

/** ランディングページ（S58、design 7 章）。ログイン済みならアプリへ。 */
export function Landing() {
  const { user, loading } = useAuth()
  const decision = landingDecision(loading, user !== null)
  if (decision === 'wait') return null
  if (decision === 'app') return <Navigate to="/app" replace />

  return (
    <div id="top" className="min-h-screen">
      <LandingHeader />
      <main className="mx-auto w-full max-w-6xl px-4 sm:px-6">
        <section className="grid items-center gap-10 py-14 sm:py-20 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)]">
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-[0.12em] text-primary">{HERO.eyebrow}</p>
            <h1 className="mt-3 text-4xl leading-tight font-extrabold tracking-tight sm:text-5xl">
              {HERO.titleLead}
              <br />
              <span className="text-brand">{HERO.titleAccent}</span>
            </h1>
            <p className="mt-5 max-w-xl leading-relaxed text-ink-2">{HERO.body}</p>
            <a href="#start" className="mt-7 inline-flex items-center gap-1 rounded-xl bg-card px-5 py-2.5 font-semibold shadow-soft transition-shadow hover:shadow-lift">
              {HERO.cta} <span aria-hidden="true">↓</span>
            </a>
          </div>
          <SamplePrCheck />
        </section>

        <section id="start" className="scroll-mt-20 py-12">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">GET STARTED</p>
          <h2 className="mt-1 text-2xl font-extrabold">{NAV.startHeading}</h2>
          <ol className="mt-8 space-y-12">
            {STEPS.map((s, i) => {
              const SampleView = STEP_SAMPLES[i]
              return (
                <li key={s.number} className="grid items-center gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                  <div className="min-w-0">
                    <p className="text-3xl font-extrabold text-ink-3/40">{s.number}</p>
                    <h3 className="mt-1 text-lg font-bold">{s.title}</h3>
                    <p className="mt-2 leading-relaxed text-ink-2">{s.body}</p>
                  </div>
                  <SampleView />
                </li>
              )
            })}
          </ol>
        </section>

        <section id="principles" className="scroll-mt-20 py-12">
          <h2 className="text-2xl font-extrabold">{NAV.principlesHeading}</h2>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {PRINCIPLES.map((p) => (
              <div key={p.title} className="rounded-2xl bg-card/70 p-5 shadow-soft">
                <p className="font-bold">{p.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-ink-2">{p.body}</p>
              </div>
            ))}
          </div>
        </section>

        <footer className="py-12 text-center text-sm text-ink-3">{INVITE_ONLY}</footer>
      </main>
    </div>
  )
}
```

`App.tsx`: `import { Landing } from './pages/Landing'` を足し、`{/* ランディングページは段 3 ... */}` の注釈と `<Route path="/" element={<Navigate to="/app" replace />} />` を `<Route path="/" element={<Landing />} />` に置き換える（`Navigate` の import は `Fallback` が使うので残す）。

- [ ] **Step 5: 確かめる**

Run: `cd web && npm test && npx tsc -b && npx oxlint && npm run build`
Expected: PASS（`indexHtml.test.ts` を含む）

- [ ] **Step 6: モックで見る**

Run: `cd web && VITE_MOCK=1 npx vite --host 127.0.0.1 --port 5173 --strictPort`（背景で起動）
Expected: 未ログインで `/` にランディングページ、ログイン後に `/` を開くと `/app` へ。ブラウザのツールが使えなければ、見た目の確認はデプロイ後にユーザーへ依頼する（390px・ライト / ダーク・動きを減らす設定）

- [ ] **Step 7: コミット**

```bash
git add web/index.html web/src/services/indexHtml.test.ts web/src/components/landing/LandingHeader.tsx web/src/pages/Landing.tsx web/src/App.tsx
git commit -m "feat(web): ランディングページとタイトル・OGP (#78)"
```
