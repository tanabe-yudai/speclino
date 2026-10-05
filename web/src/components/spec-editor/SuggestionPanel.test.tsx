import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SuggestionPanel } from './SuggestionPanel'

const render = (blockedReason: string | null) =>
  renderToStaticMarkup(<SuggestionPanel slug="ws" markdown="# 仕様" domain={null} legacyIr={[]} blockedReason={blockedReason} onApply={() => {}} />)

describe('SuggestionPanel', () => {
  it('offers to ask for suggestions and says that applying does not save or approve', () => {
    const html = render(null)
    expect(html).toContain('AI に提案を求める')
    expect(html).not.toMatch(/<button[^>]* disabled=""/)
    expect(html).toContain('保存と承認はあなたが行います')
  })

  it('disables the request and shows why while saving or previewing', () => {
    const html = render('保存中は提案を求められません。')
    expect(html).toMatch(/<button[^>]* disabled=""/)
    expect(html).toContain('保存中は提案を求められません。')
  })
})
