import { useEffect, useState } from 'react'
import { ToneBadge } from '@/components/app/ToneBadge'
import { renderMermaid, type MermaidRenderResult } from '@/services/mermaidRender'
import { mermaidBlocks, type MermaidBlock } from '@/services/specPreview'
import type { MermaidPreviewProps } from '@/types/specEditor'

interface Rendered {
  block: MermaidBlock
  result: MermaidRenderResult
}

const DEBOUNCE_MS = 500

/** 本文の ```mermaid ブロックを mermaid で描く。描画できないものは「描画できない」として別に出す。 */
export function MermaidPreview({ markdown, onJump }: MermaidPreviewProps) {
  const [rendered, setRendered] = useState<Rendered[] | null>(null)

  useEffect(() => {
    let alive = true
    const timer = setTimeout(() => {
      const blocks = mermaidBlocks(markdown)
      // 1 つずつ描く（mermaid の描画は並行に呼ぶと崩れることがある）
      ;(async () => {
        const out: Rendered[] = []
        for (const block of blocks) {
          if (!alive) return
          out.push({ block, result: await renderMermaid(block.source) })
        }
        if (alive) setRendered(out)
      })()
    }, DEBOUNCE_MS)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [markdown])

  if (rendered === null) return <p className="text-sm text-ink-3">図を描いています…</p>
  if (rendered.length === 0) return <p className="text-sm text-ink-3">```mermaid のブロックがありません。</p>

  const failures = rendered.filter((r) => !r.result.ok)
  return (
    <div className="space-y-4">
      {failures.length > 0 && (
        <section aria-label="描画できない図" className="space-y-2">
          <h3 className="text-sm font-semibold">描画できない（{failures.length} 件）</h3>
          <p className="text-xs text-ink-3">mermaid で描けるかの確認です。SpecLino が意味を読めるかは「診断」を見てください。</p>
          <ul className="space-y-2">
            {failures.map(({ block, result }) => (
              <li key={block.index}>
                <button
                  type="button"
                  className="row block w-full px-3 py-2 text-left hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                  onClick={() => onJump(block.line, 1)}
                  aria-label={`${block.line} 行の図へ移る`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <ToneBadge tone="bad">描画できない</ToneBadge>
                    <span className="text-xs text-ink-3">
                      {block.line} 行{block.id ? ` · 図 ${block.id}` : ''}
                    </span>
                  </span>
                  <pre className="mt-1 font-mono text-xs break-words whitespace-pre-wrap">{!result.ok && result.error}</pre>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {rendered.map(({ block, result }) =>
        result.ok ? (
          <figure key={block.index} className="rounded-md border bg-background p-3">
            <figcaption className="mb-2 text-xs text-ink-3">
              {block.id ? `図 ${block.id}` : '図'} · {block.line} 行
            </figcaption>
            {/* mermaid の strict モードが SVG を DOMPurify で無害化する */}
            <div className="overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: result.svg }} />
          </figure>
        ) : null,
      )}
    </div>
  )
}
