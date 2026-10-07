import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { requirementTrace } from '@/services/specPreview'
import type { CompiledSpec } from '@/types/knowledge'
import { CanonicalView } from './CanonicalView'
import { DiagnosticsList } from './DiagnosticsList'
import { RequirementTrace } from './RequirementTrace'

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

describe('RequirementTrace', () => {
  const compiled = {
    parser_version: 'uml-0.1',
    source_hash: 's',
    ir_hash: 'i',
    requirements: [
      { id: 'R1', level: 'mandatory', text: '在庫を確保', formalized_by: ['checkout#m1'] },
      { id: 'R3', level: 'mandatory', text: '監査ログ', formalized_by: [] },
    ],
  } as CompiledSpec

  it('marks unformalized rows and the mandatory ones left as REVIEW at the gate', () => {
    const html = renderToStaticMarkup(<RequirementTrace trace={requirementTrace(compiled)} hasCompiled />)
    expect(html).toContain('検査できる要求 1 / 2 件')
    expect(html).toContain('検査できない')
    expect(html).toContain('ゲートで要確認')
    expect(html).toContain('checkout#m1')
  })

  it('asks to fix errors first when there is no compiled spec', () => {
    const html = renderToStaticMarkup(<RequirementTrace trace={requirementTrace(null)} hasCompiled={false} />)
    expect(html).toContain('診断のエラーを直すと')
  })
})

describe('CanonicalView', () => {
  it('explains why there is no canonical form', () => {
    expect(renderToStaticMarkup(<CanonicalView canonical={null} pending={false} hasErrors />)).toContain('整形できません')
    expect(renderToStaticMarkup(<CanonicalView canonical="```mermaid" pending={false} hasErrors={false} />)).toContain('```mermaid')
  })
})
