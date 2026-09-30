# UI の土台（段 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「光の空間」のデザイントークンと shadcn/ui を導入し、ライト / ダークの切り替え・上部ピルの外枠・⌘K を作り、既存の全画面を新しい見た目と新しいルートに移す。

**Architecture:** web（Vite + React 19）に Tailwind CSS v4 と shadcn/ui を入れ、トークンは `src/styles/globals.css` の CSS 変数で持つ（`.dark` で切り替え）。ログイン後の全画面は `AppShell`（上部バー＋⌘K）の中に描く。判断（テーマの決定・⌘K の候補・パス）は `src/services/` の純粋関数にして vitest で試す。

**Tech Stack:** Vite 8、React 19、TypeScript 6、Tailwind CSS v4（`@tailwindcss/vite`）、shadcn/ui（CLI `shadcn@latest`）、vitest、oxlint

**Spec:** `design/2026-09-30-ui-redesign.md`（本計画は 2 章の段 1 と 3・4 章）

## Global Constraints

- 質感は「光の空間」。差し色は brand のグラデーション 1 本（ライト `#4f6bff → #27b7a8`、ダーク `#6f86ff → #3fd0bf`）
- 判定の 4 値の色は固定: 適合 `#1a8f6e` / 違反 `#c2410c` / 未検証 `#6b5bd6` / 要確認 `#a16207`（ダーク `#4fd1a8` / `#ff9a5c` / `#a99bff` / `#f2c94c`）
- ダークでは大きな黒い影を使わない。行は `#19223e` の面＋`rgba(111,134,255,.14)` の 1px の縁、ホバーで縁 `.36` と外側の淡い光
- 文字は Inter ＋ Noto Sans JP、ID とコードは JetBrains Mono
- テーマの既定は OS、手動の選択は `localStorage`（読めなければ OS）。初回描画でちらつかせない
- 型は `web/src/types/`、API 呼び出しは `web/src/api/`、判断は `web/src/services/`（画面に書かない）
- 個人の環境の値（GitHub アカウント名・GCP の識別子など）を画面・コード・例に書かない（例は `example-org/...`）
- 「デモ」という言葉を画面に出さない。新規登録の入口を置かない
- `prefers-reduced-motion: reduce` では動きを止める

## Review Focus

- `localStorage` が例外を投げる（プライベートブラウズ等）とき、テーマは OS に従って描画され、切り替えもその場では効く → Task 2 のテスト
- 所属ワークスペースが 0 件の利用者: 上部のピル・ホーム・⌘K が空でも壊れず「所属しているワークスペースがありません」と uid を出す → Task 4・5 のテスト
- member（owner でない）: 設定の「MCP トークン」タブと追加フォームが出ない。owner には出る → Task 5 のテスト
- 旧パス（`/workspaces/:slug/mcp-tokens`、`/workspaces/:slug/repositories`）のブックマークが新しい設定画面の該当タブに届く → Task 5 のテスト
- 長いリポジトリ名・スマホ幅（390px）で横スクロールが出ない、ピルが省略表示になる → Task 8 の確認

---

## File Structure

- `web/src/styles/globals.css`（新規）: Tailwind・shadcn の読み込み、トークン（`:root` と `.dark`）、地の光、基本の書体
- `web/src/services/theme.ts`（新規）: テーマの決定と保存（純粋関数＋例外に強い保存）
- `web/src/components/theme/ThemeProvider.tsx`・`useTheme.ts`（新規）: テーマの文脈
- `web/src/components/ui/*`（shadcn が生成）: button, card, badge, dialog, dropdown-menu, command, tooltip, tabs, sonner, skeleton, input, label
- `web/src/components/app/VerdictBadge.tsx`・`Panel.tsx`（新規）: 判定のバッジ、光の空間のパネル
- `web/src/components/shell/AppShell.tsx`・`TopBar.tsx`・`WorkspacePill.tsx`・`ProjectPill.tsx`・`AccountMenu.tsx`・`ThemeToggle.tsx`・`CommandPalette.tsx`（新規）
- `web/src/services/paths.ts`（新規）: 画面のパスを作る関数と旧パスの対応
- `web/src/services/commands.ts`（新規）: ⌘K の候補を作る純粋関数
- `web/src/hooks/useMe.ts`（新規）: `/api/me` を 1 回だけ取って共有する（今は画面ごとに取っている）
- `web/src/pages/Home.tsx`（新規。`RunList.tsx` を置き換え）、`web/src/pages/Settings.tsx`（新規。`Repositories.tsx`・`McpTokens.tsx` の中身をタブに）
- `web/src/pages/RunDetail.tsx`・`Login.tsx`・`GithubCallback.tsx` と `web/src/components/*`（作り直し）
- 削除: `web/src/App.css`、`web/src/index.css`、`web/src/components/AccountBar.tsx`・`Badge.tsx`、`web/src/pages/RunList.tsx`・`Repositories.tsx`・`McpTokens.tsx`（中身は Settings に移す）
- `.github/workflows/ci.yml`: web のジョブに `npm test` を足す

---

### Task 1: Tailwind v4・shadcn/ui・vitest を入れる

**Files:**
- Modify: `web/package.json`、`web/vite.config.ts`、`web/tsconfig.json`、`web/tsconfig.app.json`、`.github/workflows/ci.yml`
- Create: `web/components.json`（shadcn init が作る）、`web/src/lib/utils.ts`（同）、`web/src/styles/globals.css`、`web/vitest.config.ts`、`web/src/services/smoke.test.ts`

**Interfaces:**
- Produces: `@/` の別名（`web/src/`）、`cn()`（`@/lib/utils`）、`npm test`（vitest run）

- [ ] **Step 1: 依存を入れる**

```bash
cd web
npm install tailwindcss @tailwindcss/vite
npm install -D vitest
```

- [ ] **Step 2: `@` の別名と Tailwind の Vite プラグイン**

`web/vite.config.ts`:
```ts
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      // バックエンド（FastAPI, SPECLINO_ROLE=all）は make dev で :8080 に立つ。
      '/api': 'http://localhost:8080',
      '/healthz': 'http://localhost:8080',
    },
  },
})
```

`web/tsconfig.json` と `web/tsconfig.app.json` の `compilerOptions` に追加（shadcn の CLI が別名を読む）:
```json
"baseUrl": ".",
"paths": { "@/*": ["./src/*"] }
```

- [ ] **Step 3: 仮の globals.css を置いて shadcn を初期化**

`web/src/styles/globals.css`（Task 2 で中身を入れる。ここでは読み込みだけ）:
```css
@import "tailwindcss";
```
`web/src/main.tsx` の `import './index.css'` を `import './styles/globals.css'` に変える（`index.css` と `App.css` は Task 8 まで残す。`App.tsx` の `import './App.css'` もそのまま）。

```bash
npx shadcn@latest init
```
質問には: base color = neutral、CSS variables = yes、CSS ファイル = `src/styles/globals.css`。生成された `components.json` の `aliases` が `@/components`・`@/lib/utils` になっていることを確かめる。

- [ ] **Step 4: vitest の設定と疎通のテスト**

`web/vitest.config.ts`:
```ts
import path from 'node:path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
```
`web/package.json` の scripts に `"test": "vitest run"`。

`web/src/services/smoke.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { cn } from '@/lib/utils'

describe('cn', () => {
  it('Tailwind のクラスの衝突を後勝ちでまとめる', () => {
    expect(cn('p-2', 'p-4')).toBe('p-4')
  })
})
```

- [ ] **Step 5: 動くことを確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Expected: テスト 1 件 PASS、lint・型・ビルドが通る（画面の見た目はまだ旧 CSS のまま）

- [ ] **Step 6: CI に web のテストを足す**

`.github/workflows/ci.yml` の web のジョブ（lint / typecheck / build と同じ並び）に、`npm ci` の後で `npm test` を実行するステップ（名前 `test (frontend)`）を足す。既存のジョブの書き方（`working-directory: web`）に合わせる。

- [ ] **Step 7: Commit**

```bash
git add web .github/workflows/ci.yml
git commit -m "build(web): Tailwind v4・shadcn/ui・vitest を入れる"
```

---

### Task 2: トークンとテーマ（ライト / ダーク）

**Files:**
- Modify: `web/src/styles/globals.css`、`web/index.html`、`web/src/main.tsx`
- Create: `web/src/services/theme.ts`、`web/src/services/theme.test.ts`、`web/src/components/theme/ThemeProvider.tsx`、`web/src/components/theme/useTheme.ts`、`web/src/types/theme.ts`

**Interfaces:**
- Produces: `type ThemeChoice = 'light' | 'dark' | 'system'`、`type ResolvedTheme = 'light' | 'dark'`（`@/types/theme`）、`resolveTheme(choice, prefersDark): ResolvedTheme`、`readThemeChoice(storage): ThemeChoice`、`writeThemeChoice(storage, choice): void`、`THEME_STORAGE_KEY = 'speclino.theme'`（`@/services/theme`）、`ThemeProvider`、`useTheme(): { choice, resolved, setChoice }`

- [ ] **Step 1: 失敗するテストを書く**

`web/src/services/theme.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { THEME_STORAGE_KEY, readThemeChoice, resolveTheme, writeThemeChoice } from './theme'

const memory = (init: Record<string, string> = {}) => {
  const data = { ...init }
  return {
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
    data,
  }
}
const broken = {
  getItem: () => {
    throw new Error('SecurityError')
  },
  setItem: () => {
    throw new Error('QuotaExceededError')
  },
}

describe('resolveTheme', () => {
  it('system は OS の設定に従う', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })
  it('手動の選択は OS より優先する', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})

describe('readThemeChoice / writeThemeChoice', () => {
  it('保存した選択を読み戻す', () => {
    const s = memory()
    writeThemeChoice(s, 'dark')
    expect(s.data[THEME_STORAGE_KEY]).toBe('dark')
    expect(readThemeChoice(s)).toBe('dark')
  })
  it('知らない値・未保存は system', () => {
    expect(readThemeChoice(memory({ [THEME_STORAGE_KEY]: 'sepia' }))).toBe('system')
    expect(readThemeChoice(memory())).toBe('system')
  })
  it('storage が例外を投げても落ちずに system を返し、保存は黙って諦める', () => {
    expect(readThemeChoice(broken)).toBe('system')
    expect(() => writeThemeChoice(broken, 'dark')).not.toThrow()
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/theme.test.ts`
Expected: FAIL（`./theme` が無い）

- [ ] **Step 3: 実装**

`web/src/types/theme.ts`:
```ts
/** テーマ。system は OS の設定（prefers-color-scheme）に従う。 */
export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export interface ThemeStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}
```

`web/src/services/theme.ts`:
```ts
/** テーマの決定と保存。保存先が使えない（プライベートブラウズ等）ときは OS に従う。 */

import type { ResolvedTheme, ThemeChoice, ThemeStorage } from '@/types/theme'

export const THEME_STORAGE_KEY = 'speclino.theme'
const CHOICES: readonly ThemeChoice[] = ['light', 'dark', 'system']

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): ResolvedTheme {
  if (choice === 'system') return prefersDark ? 'dark' : 'light'
  return choice
}

export function readThemeChoice(storage: ThemeStorage): ThemeChoice {
  try {
    const value = storage.getItem(THEME_STORAGE_KEY)
    return CHOICES.includes(value as ThemeChoice) ? (value as ThemeChoice) : 'system'
  } catch {
    return 'system'
  }
}

export function writeThemeChoice(storage: ThemeStorage, choice: ThemeChoice): void {
  try {
    storage.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    // 保存できなくても、この画面の中では切り替えは効く
  }
}
```

- [ ] **Step 4: テストが通ることを確かめる**

Run: `cd web && npx vitest run src/services/theme.test.ts`
Expected: PASS（5 件）

- [ ] **Step 5: トークンを globals.css に入れる**

`web/src/styles/globals.css` を次で置き換える（shadcn init が入れた既定の変数は、この値で上書きする。`@theme inline` の対応づけは init が生成したものを残し、下の追加分を足す）:
```css
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:is(.dark *));

@theme inline {
  --font-sans: "Inter", "Noto Sans JP", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-ink-2: var(--ink-2);
  --color-ink-3: var(--ink-3);
  --color-surface: var(--surface);
  --color-surface-solid: var(--surface-solid);
  --color-ok: var(--ok);
  --color-ok-bg: var(--ok-bg);
  --color-bad: var(--bad);
  --color-bad-bg: var(--bad-bg);
  --color-unk: var(--unk);
  --color-unk-bg: var(--unk-bg);
  --color-rev: var(--rev);
  --color-rev-bg: var(--rev-bg);
  --radius-sm: 10px;
  --radius-md: 16px;
  --radius-lg: 22px;
}

:root {
  --radius: 16px;
  --bg: #f5f7fb;
  --glow-a: #dbe6ff;
  --glow-b: #d8f3ec;
  --glow-c: #ece4ff;
  --background: var(--bg);
  --foreground: #131c33;
  --ink-2: #3b4766;
  --ink-3: #6b7896;
  --surface: rgb(255 255 255 / 0.78);
  --surface-solid: #ffffff;
  --card: var(--surface);
  --card-foreground: var(--foreground);
  --popover: #ffffff;
  --popover-foreground: var(--foreground);
  --primary: #4f6bff;
  --primary-foreground: #ffffff;
  --brand-grad: linear-gradient(100deg, #4f6bff, #27b7a8);
  --secondary: #ffffff;
  --secondary-foreground: var(--foreground);
  --muted: #eef1f8;
  --muted-foreground: var(--ink-3);
  --accent: #eef1f8;
  --accent-foreground: var(--foreground);
  --destructive: #c2410c;
  --border: rgb(21 32 58 / 0.08);
  --input: rgb(21 32 58 / 0.12);
  --ring: #4f6bff;
  --ok: #1a8f6e;
  --ok-bg: #e4f6ef;
  --bad: #c2410c;
  --bad-bg: #fff1e6;
  --unk: #6b5bd6;
  --unk-bg: #efecff;
  --rev: #a16207;
  --rev-bg: #fdf6dc;
  --shadow-1: 0 1px 0 rgb(255 255 255 / 0.9) inset, 0 8px 24px rgb(36 64 140 / 0.08);
  --shadow-2: 0 1px 0 rgb(255 255 255 / 0.9) inset, 0 18px 48px rgb(36 64 140 / 0.14);
  --row: #ffffff;
  --row-edge: transparent;
  --row-hover-edge: transparent;
}

.dark {
  --bg: #0b1020;
  --glow-a: rgb(79 107 255 / 0.2);
  --glow-b: rgb(39 183 168 / 0.14);
  --glow-c: rgb(140 110 255 / 0.14);
  --foreground: #e8ecf8;
  --ink-2: #b7c0da;
  --ink-3: #7f8bab;
  --surface: rgb(22 30 54 / 0.72);
  --surface-solid: #19223e;
  --popover: #141b31;
  --primary: #6f86ff;
  --brand-grad: linear-gradient(100deg, #6f86ff, #3fd0bf);
  --secondary: #1b2440;
  --muted: #1b2440;
  --accent: #1b2440;
  --destructive: #ff9a5c;
  --border: rgb(255 255 255 / 0.08);
  --input: rgb(255 255 255 / 0.14);
  --ring: #6f86ff;
  --ok: #4fd1a8;
  --ok-bg: rgb(79 209 168 / 0.14);
  --bad: #ff9a5c;
  --bad-bg: rgb(255 154 92 / 0.14);
  --unk: #a99bff;
  --unk-bg: rgb(169 155 255 / 0.14);
  --rev: #f2c94c;
  --rev-bg: rgb(242 201 76 / 0.14);
  --shadow-1: 0 1px 0 rgb(255 255 255 / 0.05) inset, 0 2px 10px rgb(0 0 0 / 0.16);
  --shadow-2: 0 1px 0 rgb(255 255 255 / 0.06) inset, 0 8px 22px rgb(0 0 0 / 0.24);
  --row: #19223e;
  --row-edge: rgb(111 134 255 / 0.14);
  --row-hover-edge: rgb(111 134 255 / 0.36);
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply text-foreground font-sans antialiased;
    min-height: 100vh;
    background:
      radial-gradient(60% 50% at 0% 0%, var(--glow-a), transparent 70%),
      radial-gradient(50% 45% at 100% 20%, var(--glow-b), transparent 70%),
      radial-gradient(55% 45% at 40% 100%, var(--glow-c), transparent 70%),
      var(--bg);
    background-attachment: fixed;
  }
}

@layer utilities {
  .text-brand {
    background: var(--brand-grad);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .bg-brand {
    background: var(--brand-grad);
  }
  .shadow-soft {
    box-shadow: var(--shadow-1);
  }
  .shadow-lift {
    box-shadow: var(--shadow-2);
  }
  /* 要求・検査の行。ダークでは影ではなく面と縁で浮かせる（design 3.1） */
  .row {
    background: var(--row);
    border-radius: var(--radius-md);
    box-shadow: 0 0 0 1px var(--row-edge), var(--shadow-1);
    transition: transform 0.2s, box-shadow 0.2s, background-color 0.2s;
  }
  .row:hover {
    transform: translateY(-2px);
    box-shadow: 0 0 0 1px var(--row-hover-edge), var(--shadow-2);
  }
  @media (prefers-reduced-motion: reduce) {
    .row,
    .row:hover {
      transition: none;
      transform: none;
    }
  }
}
```
`tw-animate-css` が init で入っていなければ `npm install tw-animate-css`。

- [ ] **Step 6: 初回描画でちらつかせない（index.html）とフォント**

`web/index.html` の `<head>` に（`<title>` の前）:
```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&family=Noto+Sans+JP:wght@400;500;700;800&display=swap" rel="stylesheet" />
<script>
  // React より先に .dark を付けて、初回の描画でライトが一瞬見えないようにする（services/theme.ts と同じ規則）
  try {
    var c = localStorage.getItem('speclino.theme')
    var dark = c === 'dark' || (c !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
    if (dark) document.documentElement.classList.add('dark')
  } catch (e) {
    if (matchMedia('(prefers-color-scheme: dark)').matches) document.documentElement.classList.add('dark')
  }
</script>
```

- [ ] **Step 7: ThemeProvider と useTheme**

`web/src/components/theme/ThemeProvider.tsx`:
```tsx
import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { readThemeChoice, resolveTheme, writeThemeChoice } from '@/services/theme'
import type { ResolvedTheme, ThemeChoice } from '@/types/theme'

export interface ThemeContextValue {
  choice: ThemeChoice
  resolved: ResolvedTheme
  setChoice: (choice: ThemeChoice) => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

const darkQuery = () => window.matchMedia('(prefers-color-scheme: dark)')

/** テーマの選択を保持し、<html> の .dark を同期する。 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => readThemeChoice(window.localStorage))
  const [prefersDark, setPrefersDark] = useState(() => darkQuery().matches)

  useEffect(() => {
    const query = darkQuery()
    const onChange = (e: MediaQueryListEvent) => setPrefersDark(e.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveTheme(choice, prefersDark)
  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    writeThemeChoice(window.localStorage, next)
  }, [])

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
```
`window.localStorage` のアクセス自体が例外を投げる環境があるので、`readThemeChoice(window.localStorage)` は `safeStorage()`（`try { return window.localStorage } catch { return null }` で null なら `'system'`）を通す。`writeThemeChoice` も同様。

`web/src/components/theme/useTheme.ts`:
```ts
import { useContext } from 'react'
import { ThemeContext, type ThemeContextValue } from './ThemeProvider'

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme は ThemeProvider の中で使う')
  return value
}
```

`web/src/main.tsx` で `<App />` を `<ThemeProvider>` で包む。

- [ ] **Step 8: 確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Expected: すべて通る

- [ ] **Step 9: Commit**

```bash
git add web
git commit -m "feat(web): 光の空間のトークンとライト / ダークの切り替え"
```

---

### Task 3: 部品（shadcn の取り込みと判定のバッジ・パネル）

**Files:**
- Create: `web/src/components/ui/*`（shadcn が生成）、`web/src/components/app/VerdictBadge.tsx`、`web/src/components/app/Panel.tsx`、`web/src/services/verdictTone.ts`、`web/src/services/verdictTone.test.ts`
- Modify: `web/src/services/labels.ts`（`VERDICT_TONE` などを新しい tone 名に）

**Interfaces:**
- Consumes: `Verdict`（`web/src/types/finding.ts` の既存の型）
- Produces: `type VerdictTone = 'ok' | 'bad' | 'unk' | 'rev'`、`verdictTone(v: Verdict): VerdictTone`、`<VerdictBadge verdict={v} />`、`<Panel className?>`、`<Panel.Title>` 相当の見出し

- [ ] **Step 1: shadcn の部品を取り込む**

```bash
cd web
npx shadcn@latest add button card badge dialog dropdown-menu command tooltip tabs sonner skeleton input label
```

- [ ] **Step 2: 失敗するテスト**

`web/src/services/verdictTone.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { verdictTone } from './verdictTone'

describe('verdictTone', () => {
  it('4 値を固定の色に対応させる', () => {
    expect(verdictTone('VERIFIED_IN_SCOPE')).toBe('ok')
    expect(verdictTone('VIOLATED')).toBe('bad')
    expect(verdictTone('UNKNOWN')).toBe('unk')
    expect(verdictTone('REVIEW')).toBe('rev')
  })
})
```

- [ ] **Step 3: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/verdictTone.test.ts`
Expected: FAIL（モジュールが無い）

- [ ] **Step 4: 実装**

`web/src/services/verdictTone.ts`:
```ts
/** 判定の 4 値と色の対応（design 3.1。意味と色の対応は固定）。 */

import type { Verdict } from '@/types/finding'

export type VerdictTone = 'ok' | 'bad' | 'unk' | 'rev'

const TONE: Record<Verdict, VerdictTone> = {
  VERIFIED_IN_SCOPE: 'ok',
  VIOLATED: 'bad',
  UNKNOWN: 'unk',
  REVIEW: 'rev',
}

export function verdictTone(verdict: Verdict): VerdictTone {
  return TONE[verdict]
}
```
（`VerdictTone` 型は `web/src/types/components.ts` に移し、ここから再輸出する: 型は types/ に置く規約）

`web/src/components/app/VerdictBadge.tsx`:
```tsx
import { cn } from '@/lib/utils'
import { VERDICT_LABELS } from '@/services/labels'
import { verdictTone } from '@/services/verdictTone'
import type { Verdict } from '@/types/finding'

const TONE_CLASS = {
  ok: 'text-ok bg-ok-bg',
  bad: 'text-bad bg-bad-bg',
  unk: 'text-unk bg-unk-bg',
  rev: 'text-rev bg-rev-bg',
} as const

/** 判定の 4 値のバッジ。色の前に点を付け、色だけに頼らない。 */
export function VerdictBadge({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold',
        TONE_CLASS[verdictTone(verdict)],
        className,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {VERDICT_LABELS[verdict]}
    </span>
  )
}
```

`web/src/components/app/Panel.tsx`:
```tsx
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

/** 光の空間のパネル（半透明＋背景のぼかし＋柔らかい影）。 */
export function Panel({ className, ...props }: ComponentProps<'section'>) {
  return (
    <section
      className={cn(
        'rounded-lg border border-white/70 bg-surface p-5 shadow-soft backdrop-blur-md dark:border-white/5',
        className,
      )}
      {...props}
    />
  )
}

export function PanelTitle({ className, ...props }: ComponentProps<'h2'>) {
  return <h2 className={cn('text-sm font-semibold text-foreground', className)} {...props} />
}
```

`services/labels.ts` の `GATE_TONE` などが旧 `BadgeTone`（旧 CSS の `badge-*`）を返している箇所は、Task 6 で画面を移すときに一緒に `VerdictTone` / ゲート用の tone に置き換える（ここでは触らない）。

- [ ] **Step 5: 確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat(web): shadcn の部品と判定のバッジ・パネル"
```

---

### Task 4: 外枠（上部バー・ピル・アカウント・テーマ・⌘K）

**Files:**
- Create: `web/src/hooks/useMe.ts`、`web/src/services/paths.ts`、`web/src/services/paths.test.ts`、`web/src/services/commands.ts`、`web/src/services/commands.test.ts`、`web/src/types/command.ts`、`web/src/components/shell/AppShell.tsx`・`TopBar.tsx`・`WorkspacePill.tsx`・`ProjectPill.tsx`・`AccountMenu.tsx`・`ThemeToggle.tsx`・`CommandPalette.tsx`
- Modify: `web/src/components/RequireAuth.tsx`（Outlet を AppShell で包む）
- Delete: `web/src/components/AccountBar.tsx`

**Interfaces:**
- Consumes: `getMe()`（`@/api/me`）、`listRepositories(slug)`（`@/api/repositories`）、`useAuth()`（`signOut`）、`useTheme()`
- Produces:
  - `paths`: `paths.home()` → `/app`、`paths.project(slug, fullName)` → `/w/${slug}/p/${owner}/${repo}`、`paths.policies(slug)` → `/w/${slug}/policies`、`paths.settings(slug, tab?)` → `/w/${slug}/settings` または `?tab=${tab}`、`paths.run(id)` → `/runs/${id}`
  - `buildCommands(input: { me: Me | null; repositories: Record<string, RegisteredRepository[]> }): Command[]`（`Command = { id: string; label: string; group: 'ワークスペース' | 'プロジェクト' | '画面'; path: string }`）
  - `useMe(): { me: Me | null; error: string | null; reload(): void }`（アプリ全体で 1 回だけ取る）
  - `<AppShell>`（上部バー＋`<Outlet/>`＋⌘K）

- [ ] **Step 1: 失敗するテスト（paths と commands）**

`web/src/services/paths.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { paths } from './paths'

describe('paths', () => {
  it('プロジェクトは owner/repo をパスの 2 段にする', () => {
    expect(paths.project('ws', 'example-org/example-repo')).toBe('/w/ws/p/example-org/example-repo')
  })
  it('設定のタブはクエリで指定する', () => {
    expect(paths.settings('ws')).toBe('/w/ws/settings')
    expect(paths.settings('ws', 'tokens')).toBe('/w/ws/settings?tab=tokens')
  })
  it('slug と名前はエンコードする', () => {
    expect(paths.policies('a b')).toBe('/w/a%20b/policies')
  })
})
```

`web/src/services/commands.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { buildCommands } from './commands'
import type { Me } from '@/types/me'

const me: Me = {
  uid: 'u1',
  email: 'owner@example.com',
  email_verified: true,
  workspaces: [{ slug: 'ws', name: 'Example', role: 'owner' }],
}

describe('buildCommands', () => {
  it('ホーム・各ワークスペースの規約と設定・各プロジェクトを並べる', () => {
    const commands = buildCommands({
      me,
      repositories: {
        ws: [{ repo_full_name: 'example-org/example-repo', github_repo_id: 1, default_branch: 'main' }],
      },
    })
    expect(commands.map((c) => c.path)).toEqual([
      '/app',
      '/w/ws/p/example-org/example-repo',
      '/w/ws/policies',
      '/w/ws/settings',
    ])
  })
  it('所属が無くても落ちずにホームだけ', () => {
    expect(buildCommands({ me: { ...me, workspaces: [] }, repositories: {} }).map((c) => c.path)).toEqual(['/app'])
    expect(buildCommands({ me: null, repositories: {} }).map((c) => c.path)).toEqual(['/app'])
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/paths.test.ts src/services/commands.test.ts`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: 実装（paths・commands・型）**

`web/src/services/paths.ts`:
```ts
/** 画面のパス（design 4 章）。組み立てはここだけで行う。 */

const e = encodeURIComponent

export type SettingsTab = 'repositories' | 'tokens'

export const paths = {
  home: () => '/app',
  project: (slug: string, fullName: string) => {
    const [owner, repo] = fullName.split('/')
    return `/w/${e(slug)}/p/${e(owner)}/${e(repo)}`
  },
  policies: (slug: string) => `/w/${e(slug)}/policies`,
  settings: (slug: string, tab?: SettingsTab) => `/w/${e(slug)}/settings${tab ? `?tab=${tab}` : ''}`,
  run: (id: string) => `/runs/${e(id)}`,
}
```

`web/src/types/command.ts`:
```ts
export type CommandGroup = 'ワークスペース' | 'プロジェクト' | '画面'

export interface Command {
  id: string
  label: string
  group: CommandGroup
  path: string
}
```

`web/src/services/commands.ts`:
```ts
/** ⌘K の候補。所属ワークスペースと登録リポジトリから作る（ネットワークは呼ばない）。 */

import { paths } from './paths'
import type { Command } from '@/types/command'
import type { Me } from '@/types/me'
import type { RegisteredRepository } from '@/types/repository'

export function buildCommands(input: {
  me: Me | null
  repositories: Record<string, RegisteredRepository[]>
}): Command[] {
  const commands: Command[] = [{ id: 'home', label: 'ホーム', group: '画面', path: paths.home() }]
  for (const ws of input.me?.workspaces ?? []) {
    const label = ws.name ?? ws.slug
    for (const repo of input.repositories[ws.slug] ?? []) {
      commands.push({
        id: `project:${ws.slug}:${repo.repo_full_name}`,
        label: repo.repo_full_name,
        group: 'プロジェクト',
        path: paths.project(ws.slug, repo.repo_full_name),
      })
    }
    commands.push({ id: `policies:${ws.slug}`, label: `${label} の規約`, group: 'ワークスペース', path: paths.policies(ws.slug) })
    commands.push({ id: `settings:${ws.slug}`, label: `${label} の設定`, group: 'ワークスペース', path: paths.settings(ws.slug) })
  }
  return commands
}
```

- [ ] **Step 4: テストが通ることを確かめる**

Run: `cd web && npx vitest run src/services/paths.test.ts src/services/commands.test.ts`
Expected: PASS

- [ ] **Step 5: useMe（1 回だけ取って共有）**

`web/src/hooks/useMe.ts`:
```ts
import { createContext, useContext } from 'react'
import type { Me } from '@/types/me'

export interface MeState {
  me: Me | null
  error: string | null
  reload: () => void
}

export const MeContext = createContext<MeState | null>(null)

/** AppShell が 1 回だけ取った /api/me を読む（画面ごとに取り直さない）。 */
export function useMe(): MeState {
  const value = useContext(MeContext)
  if (!value) throw new Error('useMe は AppShell の中で使う')
  return value
}
```

- [ ] **Step 6: 外枠の部品**

`web/src/components/shell/AppShell.tsx`:
```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { getMe } from '@/api/me'
import { ApiError } from '@/api/errors'
import { MeContext } from '@/hooks/useMe'
import type { Me } from '@/types/me'
import { CommandPalette } from './CommandPalette'
import { TopBar } from './TopBar'

/** ログイン後の全画面の外枠（上部バー・⌘K）。/api/me はここで 1 回だけ取る。 */
export function AppShell() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)

  const reload = useCallback(() => {
    getMe()
      .then((m) => {
        setMe(m)
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : '利用者の情報を取得できませんでした'))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const value = useMemo(() => ({ me, error, reload }), [me, error, reload])
  return (
    <MeContext.Provider value={value}>
      <TopBar onOpenPalette={() => setPaletteOpen(true)} />
      <main className="mx-auto w-full max-w-6xl px-4 pb-16 pt-6 sm:px-6">
        <Outlet />
      </main>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </MeContext.Provider>
  )
}
```

`TopBar.tsx`: `sticky top-0 z-40` の帯（`bg-background/70 backdrop-blur-md border-b`）。左に `<Link to={paths.home()}>` のロゴ（`◆ SpecLino`、`font-extrabold`）、`<WorkspacePill/>`、`<ProjectPill/>`、`<NavLink>` の「規約」「設定」（現在のワークスペースがあるときだけ。現在のワークスペースは URL の `:slug`、無ければ `me.workspaces[0]`）。右に ⌘K のボタン（`<kbd>⌘K</kbd>` を表示し `onOpenPalette`）、`<ThemeToggle/>`、`<AccountMenu/>`。スマホ幅ではピルの文字を `max-w-[9rem] truncate` で省略する。

`WorkspacePill.tsx`: shadcn の `DropdownMenu`。トリガーは丸いピル（`rounded-full bg-secondary px-3.5 py-1.5 shadow-soft`）に現在のワークスペース名。項目は `me.workspaces`（名前と role のラベル `WORKSPACE_ROLE_LABELS`）で、選ぶと `paths.home()` の `?w=<slug>` ではなく、現在の画面の `:slug` を差し替えたパスへ（プロジェクト画面ならそのワークスペースのホームへ）。所属が無ければピル自体を出さない。

`ProjectPill.tsx`: 現在のワークスペースの登録リポジトリ（`listRepositories(slug)`）を DropdownMenu に並べ、選ぶと `paths.project(slug, name)`。リポジトリが 0 件なら「リポジトリを追加」（`paths.settings(slug, 'repositories')`）だけ。（プロジェクト画面そのものは段 2 で作る。段 1 ではリンク先が未実装なので、段 1 のうちは `paths.project` への遷移先を「準備中」の画面にする: App のルートに `/w/:slug/p/:owner/:repo` を置き、`<Panel>` に「このリポジトリの画面は準備中です」と検査の一覧（そのリポジトリの run だけ）を出す）

`AccountMenu.tsx`: `DropdownMenu`。トリガーはイニシャルの丸（メールの先頭 2 文字）。中身: メールアドレス、所属ワークスペースと role の一覧、「ログアウト」（`useAuth().signOut` → `/login`）。所属が無ければ「所属しているワークスペースがありません」と uid（コピーできる）。

`ThemeToggle.tsx`: `Button variant="ghost" size="icon"`。クリックで `resolved` の逆を `setChoice`。長押し・右クリックはしない（3 状態の選択は AccountMenu の中に「テーマ: OS に合わせる / ライト / ダーク」として置く）。

`CommandPalette.tsx`: shadcn の `CommandDialog`。`buildCommands({ me, repositories })` の候補を group ごとに出し、選ぶと `navigate(path)` して閉じる。repositories は開いたときに、所属ワークスペースごとに `listRepositories` を並行で取る（失敗したワークスペースは候補から外すだけ）。

`RequireAuth.tsx`: `return <Outlet />` を `return <AppShell />` にする（AppShell が Outlet を描く）。`AccountBar` の import を消し、`AccountBar.tsx` を削除する。

- [ ] **Step 7: 確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Expected: 通る。`VITE_MOCK=1 npm run dev` を開き、上部バー・ピル・アカウント・テーマの切り替え・⌘K（⌘K / Ctrl+K で開閉）が動く

- [ ] **Step 8: Commit**

```bash
git add web
git commit -m "feat(web): 上部のピル・アカウント・テーマ・⌘K の外枠"
```

---

### Task 5: ホーム・設定・ルートと旧パスの転送

**Files:**
- Create: `web/src/pages/Home.tsx`、`web/src/pages/Settings.tsx`、`web/src/pages/ProjectPlaceholder.tsx`、`web/src/services/legacyRoutes.ts`、`web/src/services/legacyRoutes.test.ts`、`web/src/services/settingsTabs.ts`、`web/src/services/settingsTabs.test.ts`
- Modify: `web/src/App.tsx`、`web/src/pages/Login.tsx`（ログイン後の行き先を `/app` に）
- Delete: `web/src/pages/RunList.tsx`・`Repositories.tsx`・`McpTokens.tsx`（中身を Home / Settings に移してから）

**Interfaces:**
- Consumes: `paths`、`useMe()`、`listRuns()`・`listRepositories()`・`listMcpTokens()` など既存の API、`VerdictBadge`・`Panel`
- Produces: `legacyRedirect(pathname: string): string | null`、`visibleSettingsTabs(role: WorkspaceRole | null): SettingsTab[]`

- [ ] **Step 1: 失敗するテスト**

`web/src/services/legacyRoutes.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { legacyRedirect } from './legacyRoutes'

describe('legacyRedirect', () => {
  it('旧い MCP トークンとリポジトリの画面を設定のタブへ', () => {
    expect(legacyRedirect('/workspaces/ws/mcp-tokens')).toBe('/w/ws/settings?tab=tokens')
    expect(legacyRedirect('/workspaces/ws/repositories')).toBe('/w/ws/settings?tab=repositories')
  })
  it('関係ないパスは null', () => {
    expect(legacyRedirect('/runs/abc')).toBeNull()
    expect(legacyRedirect('/workspaces/ws/other')).toBeNull()
  })
})
```

`web/src/services/settingsTabs.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { visibleSettingsTabs } from './settingsTabs'

describe('visibleSettingsTabs', () => {
  it('owner はリポジトリと MCP トークン', () => {
    expect(visibleSettingsTabs('owner')).toEqual(['repositories', 'tokens'])
  })
  it('owner 以外・非メンバーはリポジトリだけ（MCP トークンは owner の操作）', () => {
    expect(visibleSettingsTabs('approver')).toEqual(['repositories'])
    expect(visibleSettingsTabs('member')).toEqual(['repositories'])
    expect(visibleSettingsTabs(null)).toEqual(['repositories'])
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/legacyRoutes.test.ts src/services/settingsTabs.test.ts`
Expected: FAIL

- [ ] **Step 3: 実装**

`web/src/services/legacyRoutes.ts`:
```ts
/** 旧いパス（ブックマーク）を新しい画面へ（design 4 章）。 */

import { paths } from './paths'

const LEGACY = /^\/workspaces\/([^/]+)\/(mcp-tokens|repositories)\/?$/

export function legacyRedirect(pathname: string): string | null {
  const m = LEGACY.exec(pathname)
  if (!m) return null
  const slug = decodeURIComponent(m[1])
  return paths.settings(slug, m[2] === 'mcp-tokens' ? 'tokens' : 'repositories')
}
```

`web/src/services/settingsTabs.ts`:
```ts
import type { SettingsTab } from './paths'
import type { WorkspaceRole } from '@/types/me'

export function visibleSettingsTabs(role: WorkspaceRole | null): SettingsTab[] {
  return role === 'owner' ? ['repositories', 'tokens'] : ['repositories']
}
```

- [ ] **Step 4: テストが通ることを確かめる**

Run: `cd web && npx vitest run src/services/legacyRoutes.test.ts src/services/settingsTabs.test.ts`
Expected: PASS

- [ ] **Step 5: ルート**

`web/src/App.tsx`:
```tsx
import { Navigate, Route, Routes, BrowserRouter, useLocation } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './components/RequireAuth'
import { legacyRedirect } from './services/legacyRoutes'
import { GithubCallback } from './pages/GithubCallback'
import { Home } from './pages/Home'
import { Login } from './pages/Login'
import { ProjectPlaceholder } from './pages/ProjectPlaceholder'
import { RunDetail } from './pages/RunDetail'
import { Settings } from './pages/Settings'

function Fallback() {
  const { pathname } = useLocation()
  return <Navigate to={legacyRedirect(pathname) ?? '/app'} replace />
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* ランディングページは段 3。それまでは / をホームへ送る */}
          <Route path="/" element={<Navigate to="/app" replace />} />
          <Route element={<RequireAuth />}>
            <Route path="/app" element={<Home />} />
            <Route path="/w/:slug/p/:owner/:repo" element={<ProjectPlaceholder />} />
            <Route path="/w/:slug/settings" element={<Settings />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/github/callback" element={<GithubCallback />} />
          </Route>
          <Route path="*" element={<Fallback />} />
        </Routes>
        <Toaster />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
```
`/w/:slug/policies` は段 4 で足す（それまで上部の「規約」リンクは出さない: TopBar の該当リンクは `import.meta.env` ではなく、ルートが用意できた段で足す。段 1 では「設定」だけ）。

- [ ] **Step 6: ホーム**

`web/src/pages/Home.tsx`（旧 `RunList.tsx` の置き換え）: 上から
1. 見出し「ホーム」と所属ワークスペース名
2. プロジェクトのカード（現在のワークスペースの `listRepositories`。カードは `Panel` に `repo_full_name`、既定ブランチ、`paths.project` へのリンク。健康状態のリングは段 2 で足すので、段 1 は名前だけ）。リポジトリが無ければ「リポジトリを追加」（owner だけ。`paths.settings(slug, 'repositories')`）
3. 最近の検査（`listRuns(50)` を `row` クラスの行で。対象（`repo#pr`）・head（mono）・ゲートのバッジ・実行状態・時刻。クリックで `paths.run(id)`）
4. 所属が無ければ（`me.workspaces.length === 0`）「所属しているワークスペースがありません。管理者にこの uid を伝えて追加してもらってください」と uid

読み込み中は `Skeleton`、失敗は `ErrorState`（Task 6 で作り直す既存の部品）。

- [ ] **Step 7: 設定**

`web/src/pages/Settings.tsx`: `Tabs`。タブは `visibleSettingsTabs(role)`、既定は `?tab=` の値（無効なら先頭）。
- 「リポジトリ」タブ: 旧 `Repositories.tsx` の中身（一覧、owner だけ追加フォーム、`startRegistration`）を新しい部品（`Panel`、`Input`、`Button`）で
- 「MCP トークン」タブ: 旧 `McpTokens.tsx` の中身（一覧、発行、平文の 1 回表示とコピー、失効は `Dialog` で確認、結果は `toast`）
- 旧 `Repositories.tsx`・`McpTokens.tsx`・`RunList.tsx` を削除し、参照（`App.tsx`・`AccountBar` の遺物）が無いことを `npx tsc -b` で確かめる

`ProjectPlaceholder.tsx`: `Panel` に「このリポジトリの画面は準備中です」と、そのリポジトリの run（`listRuns(50)` を `repo_full_name` で絞る）の行。

`Login.tsx`: ログイン後の遷移先を `location.state.from` か `/app` にする（今は `/`）。

- [ ] **Step 8: 確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Expected: 通る。`VITE_MOCK=1 npm run dev` で `/app`、`/w/demo/settings`、`/w/demo/settings?tab=tokens`、`/workspaces/demo/mcp-tokens`（転送される）を開いて確かめる

- [ ] **Step 9: Commit**

```bash
git add web
git commit -m "feat(web): ホーム・設定と新しいルート（旧パスは転送）"
```

---

### Task 6: 検査の詳細を新しい見た目に

**Files:**
- Modify: `web/src/pages/RunDetail.tsx`、`web/src/components/FindingCard.tsx`・`CounterexamplePanel.tsx`・`ScopeBlock.tsx`・`Timeline.tsx`・`RerunButton.tsx`・`ErrorState.tsx`・`LoadingState.tsx`、`web/src/services/labels.ts`
- Delete: `web/src/components/Badge.tsx`（`VerdictBadge` と shadcn の `Badge` に置き換え）
- Test: `web/src/services/gateTone.test.ts`

**Interfaces:**
- Consumes: `VerdictBadge`、`Panel`、`verdictTone`、shadcn の `Tabs`・`Badge`・`Button`・`Skeleton`
- Produces: `gateTone(status: GateStatus): VerdictTone`（ゲート pass → ok、pass_with_exceptions → rev、fail → bad）

- [ ] **Step 1: 失敗するテスト**

`web/src/services/gateTone.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { gateTone } from './labels'

describe('gateTone', () => {
  it('ゲートの結果を判定の色に対応させる', () => {
    expect(gateTone('pass')).toBe('ok')
    expect(gateTone('pass_with_exceptions')).toBe('rev')
    expect(gateTone('fail')).toBe('bad')
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/gateTone.test.ts`
Expected: FAIL（`gateTone` が無い）

- [ ] **Step 3: 実装**

`web/src/services/labels.ts` に（既存の `GATE_TONE` を置き換え、参照箇所も直す）:
```ts
import type { VerdictTone } from '@/types/components'

export function gateTone(status: GateStatus): VerdictTone {
  return status === 'pass' ? 'ok' : status === 'pass_with_exceptions' ? 'rev' : 'bad'
}
```
ゲートのバッジは `VerdictBadge` と同じ見た目の小さな部品 `GateBadge`（`components/app/GateBadge.tsx`、`GATE_STATUS_LABELS` と `gateTone`）。

画面の作り直し（振る舞いは変えない。ポーリング・タブ・再実行・Markdown の表示はそのまま）:
- 上部の要約を `Panel` に: 対象（`repo#pr`、GitHub へのリンク）、head（mono）、`GateBadge`、実行状態、4 値の件数を `VerdictBadge` と数で横に並べる、ゲートの理由、意味差分（`describeSemanticDiff`）、再実行（`RerunButton` を `Button` に）
- 所見（`FindingCard`）は `row` クラスのカード。見出しに要求 ID（mono）と `VerdictBadge`、違反は左端に `bad` の細い線。反例（`CounterexamplePanel`）は mono の地（`bg-muted rounded-md p-3`）
- スコープと前提（`ScopeBlock`）、タイムライン（`Timeline`）は `Panel` の中。タイムラインの点は 4 値の色
- 「タイムライン / レビュー（Markdown）」は shadcn の `Tabs`。Markdown は `prose` 相当のクラス（`[&_h2]:text-lg [&_code]:font-mono` など、Tailwind のみで）
- 読み込み中は `Skeleton`、失敗は `ErrorState`（`Panel` にメッセージと「再試行」`Button`）
- 旧 `Badge.tsx` を削除し、`BadgeTone` の型も `types/components.ts` から消す

- [ ] **Step 4: 確かめる**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Expected: 通る。`VITE_MOCK=1 npm run dev` で検査の詳細（fixture の run）をライト / ダークで確かめる

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat(web): 検査の詳細を新しい見た目に"
```

---

### Task 7: ログインとコールバックを新しい見た目に

**Files:**
- Modify: `web/src/pages/Login.tsx`、`web/src/pages/GithubCallback.tsx`

**Interfaces:**
- Consumes: `Panel`、shadcn の `Input`・`Label`・`Button`、`useAuth()`

- [ ] **Step 1: ログイン**

画面の中央に `Panel`（幅 `max-w-sm`）。上に `◆ SpecLino` とひとこと（「仕様どおりか、確かめる。」の「仕様どおりか」を `text-brand`）、メールとパスワード（`Label` ＋ `Input`）、`Button`（`bg-brand text-white`）「ログイン」。エラーは `text-bad`。dev モード（Firebase の設定が無いとき）の「開発モードで入る」ボタンは今の条件のまま `Button variant="secondary"` で残す（画面の文言に「デモ」を使わない）。ログイン済みなら `/app` へ。右上にテーマ切り替え（`ThemeToggle`。AppShell の外なので単独で置く）。

- [ ] **Step 2: コールバック**

`Panel` の中に、確認中は `Skeleton` と「GitHub で確認しています…」、成功は `VerdictBadge` 相当の緑の点と「<repo> を登録しました / すでに登録済みです」と設定のリポジトリタブへのリンク（`paths.settings(slug, 'repositories')`）、失敗は理由と対処（今の文言）。二重送信の防止（`useRef`）と、パラメータ欠落を描画時に導く形は今の実装のまま。

- [ ] **Step 3: 確かめる**

Run: `cd web && npm run lint && npx tsc -b && npm run build`
Expected: 通る。`VITE_MOCK=1 npm run dev` で `/login` と `/github/callback?code=mock&state=mock` をライト / ダークで確かめる

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "feat(web): ログインとコールバックを新しい見た目に"
```

---

### Task 8: 旧 CSS の削除と仕上げの確認

**Files:**
- Delete: `web/src/App.css`、`web/src/index.css`
- Modify: `web/src/App.tsx`・`web/src/main.tsx`（旧 CSS の import を消す）、`web/src/types/components.ts`（使われなくなった型）

- [ ] **Step 1: 旧 CSS を消す**

`web/src/App.css`・`web/src/index.css` を削除し、import を消す。`git grep -n "className=\"" web/src | grep -E "run-|finding-card|badge-|state-panel|tab-bar|scope-|timeline-|login-|mcp-token|inline-form|form-error|account-bar|repository-list"` が 0 件になるまで、残った旧クラスを Tailwind のクラスに置き換える。

- [ ] **Step 2: 全部の検査**

Run: `cd web && npm test && npm run lint && npx tsc -b && npm run build`
Run（リポジトリ直下）: `UV_NO_SYNC=1 uv run pytest tests/unit/test_smoke.py -q`（SPA の配信と `index.html` の no-cache。ビルド済みの `web/dist` を使う）
Expected: すべて通る

- [ ] **Step 3: 画面の確認（ライト / ダーク × PC / スマホ幅 390px）**

`cd web && VITE_MOCK=1 npm run dev -- --host 127.0.0.1` を起動し、VS Code のポート転送で開く。確かめること:
- `/login`、`/app`、`/w/demo/settings`（両タブ）、`/runs/<fixture の id>`、`/github/callback?code=mock&state=mock`
- テーマの切り替え（ボタンと AccountMenu の 3 択）、再読み込みしてもちらつかない
- ⌘K（Ctrl+K）で開き、プロジェクト・設定へ移動できる
- スマホ幅で横スクロールが出ない。長いリポジトリ名のピルが省略表示になる
- 所属 0 件の見え方（`web/src/api/me.ts` の mock を一時的に `workspaces: []` にして確かめ、戻す）
- OS の「視差効果を減らす」で行のホバーの動きが止まる

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "refactor(web): 旧 CSS を消して新しいトークンに一本化"
```
