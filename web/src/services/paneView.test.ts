import { describe, expect, it } from 'vitest'
import { initialPaneView, paneViewLabel } from './paneView'

describe('initialPaneView', () => {
  it('opens the preview for existing text and the editor for new (blank) text', () => {
    expect(initialPaneView('# 仕様')).toBe('preview')
    expect(initialPaneView('')).toBe('edit')
    expect(initialPaneView('  \n')).toBe('edit')
  })
})

describe('paneViewLabel', () => {
  it('adds the diagnostic count to the preview side only when there are diagnostics', () => {
    expect(paneViewLabel('preview', 3)).toBe('プレビュー（診断 3）')
    expect(paneViewLabel('preview', 0)).toBe('プレビュー')
    expect(paneViewLabel('preview', null)).toBe('プレビュー')
    expect(paneViewLabel('edit', 3)).toBe('本文')
  })
})
