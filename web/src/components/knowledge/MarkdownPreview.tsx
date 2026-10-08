import { useEffect, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { renderMermaid, type MermaidRenderResult } from '@/services/mermaidRender'
import { splitSkillMd } from '@/services/skillFrontmatter'
import type { MarkdownPreviewProps } from '@/types/specEditor'

const PROSE =
  'text-sm leading-relaxed [overflow-wrap:break-word] [&_a]:text-primary [&_a]:underline [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-ink-3 [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs [&_h1]:mt-4 [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-bold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-4 [&_h3]:mb-1 [&_h3]:font-semibold [&_hr]:my-4 [&_ol]:my-2 [&_ol]:ml-5 [&_ol]:list-decimal [&_p]:my-2 [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:my-3 [&_table]:block [&_table]:overflow-x-auto [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:px-2 [&_th]:py-1 [&_ul]:my-2 [&_ul]:ml-5 [&_ul]:list-disc'

/** ```mermaid の 1 ブロックを描く（描画は services/mermaidRender.ts を共用）。 */
function MermaidFigure({ source }: { source: string }) {
  const [result, setResult] = useState<MermaidRenderResult | null>(null)
  useEffect(() => {
    let alive = true
    const timer = setTimeout(() => {
      renderMermaid(source).then((r) => alive && setResult(r))
    }, 500)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [source])
  if (!result) return <p className="text-xs text-ink-3">図を描いています…</p>
  if (!result.ok) {
    return <pre className="font-mono text-xs break-words whitespace-pre-wrap text-bad">図を描画できません: {result.error}</pre>
  }
  // mermaid の strict モードが SVG を DOMPurify で無害化する
  return <div className="overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: result.svg }} />
}

// 生の HTML は描かない（react-markdown の既定。rehype-raw は使わない）。リンクは別タブで開く
const components: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}

const componentsWithMermaid: Components = {
  ...components,
  pre: ({ node, children, ...props }) => {
    const code = node?.children[0]
    if (code?.type === 'element' && code.tagName === 'code') {
      const classes = code.properties?.className
      if (Array.isArray(classes) && classes.includes('language-mermaid')) {
        const text = code.children.map((c) => (c.type === 'text' ? c.value : '')).join('')
        return <MermaidFigure source={text} />
      }
    }
    return <pre {...props}>{children}</pre>
  },
}

/** Markdown の本文を描く。空なら案内を出す。 */
export function MarkdownPreview({ markdown, mermaid = false, skillMd = false }: MarkdownPreviewProps) {
  if (!markdown.trim()) return <p className="text-sm text-ink-3">本文を書くとここに表示されます</p>
  const parts = skillMd ? splitSkillMd(markdown) : { name: null, description: null, body: markdown }
  return (
    <div className={PROSE}>
      {parts.name && <h1 className="!mt-0 font-mono">{parts.name}</h1>}
      {parts.description && <p className="text-base text-ink-3">{parts.description}</p>}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mermaid ? componentsWithMermaid : components}>
        {parts.body}
      </ReactMarkdown>
    </div>
  )
}
