import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { DiagnosticsList } from './DiagnosticsList'

describe('DiagnosticsList', () => {
  it('shows located diagnostics as buttons that jump to the line, errors first', () => {
    const html = renderToStaticMarkup(
      <DiagnosticsList
        onJump={() => {}}
        diagnostics={[
          { code: 'W_RESERVED_WORD', severity: 'warning', message: '予約語', span: { diagram_id: 'f', line: 2, column: 1 } },
          { code: 'E_GUARD_SYNTAX', severity: 'error', message: '自然言語のガード', span: { diagram_id: 'checkout', line: 9, column: 5 } },
          { code: 'E_TOO_LARGE', severity: 'error', message: '大きすぎる', span: null },
        ]}
      />,
    )
    expect(html.indexOf('E_GUARD_SYNTAX')).toBeLessThan(html.indexOf('E_TOO_LARGE'))
    expect(html.indexOf('E_TOO_LARGE')).toBeLessThan(html.indexOf('W_RESERVED_WORD'))
    expect(html).toContain('aria-label="9 行 5 列へ移る: 自然言語のガード"')
    expect(html).toContain('図 checkout')
    expect(html).toContain('場所なし')
  })

  it('says when there is nothing to report', () => {
    expect(renderToStaticMarkup(<DiagnosticsList diagnostics={[]} onJump={() => {}} />)).toContain('診断はありません')
  })
})
