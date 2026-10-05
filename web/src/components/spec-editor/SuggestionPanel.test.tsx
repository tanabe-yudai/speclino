import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SuggestionPanel } from './SuggestionPanel'

const render = (blockedReason: string | null) =>
  renderToStaticMarkup(<SuggestionPanel state={{ kind: 'idle' }} stale={false} blockedReason={blockedReason} onRequest={() => {}} onApply={() => {}} onReject={() => {}} />)

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

  it('keeps the suggestions it is given (state lives in the editor) and disables apply when stale', () => {
    const after = { errors_before: 2, errors_after: 0, compiles: true, diagnostics_after: [] }
    const state = { kind: 'ready' as const, base: 'a\nb\n', items: [{ id: 0, start: 2, end: 4, replacement: 'B\n', reason: 'ガードを式にする', after }], errorsBefore: 2, dropped: 0 }
    const fresh = renderToStaticMarkup(<SuggestionPanel state={state} stale={false} blockedReason={null} onRequest={() => {}} onApply={() => {}} onReject={() => {}} />)
    expect(fresh).toContain('ガードを式にする')
    expect(fresh).toContain('エラー 2 → 0 件')
    expect(fresh).not.toMatch(/<button[^>]* disabled=""[^>]*>適用/)
    const stale = renderToStaticMarkup(<SuggestionPanel state={state} stale blockedReason={null} onRequest={() => {}} onApply={() => {}} onReject={() => {}} />)
    expect(stale).toMatch(/<button[^>]* disabled=""[^>]*>適用/)
    expect(stale).toContain('もう一度提案を求めてください')
  })
})
