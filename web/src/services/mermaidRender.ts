/**
 * mermaid の描画（別チャンクで読み込む）。描画できるかは S63 の parser の診断とは別の確認。
 * securityLevel は strict（ラベルの HTML・クリックを許さない）。
 */

type Mermaid = typeof import('mermaid').default

let loaded: { theme: string; mermaid: Promise<Mermaid> } | null = null

function currentTheme(): 'dark' | 'default' {
  return typeof document !== 'undefined' && document.documentElement.classList.contains('dark') ? 'dark' : 'default'
}

function load(): Promise<Mermaid> {
  const theme = currentTheme()
  if (loaded?.theme === theme) return loaded.mermaid
  const mermaid = import('mermaid').then(({ default: m }) => {
    m.initialize({ startOnLoad: false, securityLevel: 'strict', theme, suppressErrorRendering: true })
    return m
  })
  loaded = { theme, mermaid }
  return mermaid
}

let counter = 0

export type MermaidRenderResult = { ok: true; svg: string } | { ok: false; error: string }

export async function renderMermaid(source: string): Promise<MermaidRenderResult> {
  try {
    const mermaid = await load()
    const { svg } = await mermaid.render(`speclino-mermaid-${++counter}`, source)
    return { ok: true, svg }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return { ok: false, error: message.split('\n').slice(0, 6).join('\n') }
  }
}
