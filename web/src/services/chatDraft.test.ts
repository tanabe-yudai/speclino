import { describe, expect, it } from 'vitest'
import { bodyText, canUndo, decideAutoApply, draftDiff, restoreApplied, skipMessage, touchesFiles, withBodyText } from './chatDraft'
import type { ArtifactContent } from '@/types/knowledge'
import type { ChatProposal } from '@/types/knowledgeChat'

const scope = { workspace: true, projects: [] }
const skill: ArtifactContent = { kind: 'skill', skill_md: 'S', files: { 'a.md': 'A' }, scope }
const instr: ArtifactContent = { kind: 'instructions', target: 'claude_md', body: 'B', scope }
const spec: ArtifactContent = { kind: 'spec', markdown: 'M', domain: null, legacy_ir: [], scope, compiled: null }
const proposal = (over: Partial<ChatProposal> = {}): ChatProposal => ({ base_hash: 'sha256:x', raw_proposal: {}, diagnostics: [], valid: true, ...over })

describe('bodyText / withBodyText', () => {
  it('picks the main field per kind and replaces only it', () => {
    expect([bodyText(spec), bodyText(instr), bodyText(skill)]).toEqual(['M', 'B', 'S'])
    expect(withBodyText(skill, 'T')).toEqual({ ...skill, skill_md: 'T' })
    expect(withBodyText(instr, 'T')).toEqual({ ...instr, body: 'T' })
    expect(withBodyText(spec, 'T')).toEqual({ ...spec, markdown: 'T' })
  })
})

describe('touchesFiles', () => {
  it('is false without files, for same files, and for non-skill', () => {
    expect(touchesFiles(skill, { skill_md: 'x' })).toBe(false)
    expect(touchesFiles(skill, { skill_md: 'x', files: { 'a.md': 'A' } })).toBe(false)
    expect(touchesFiles(instr, { body: 'x' })).toBe(false)
  })
  it('detects add, change and delete', () => {
    expect(touchesFiles(skill, { skill_md: 'x', files: { 'a.md': 'A', 'b.md': 'B' } })).toBe(true)
    expect(touchesFiles(skill, { skill_md: 'x', files: { 'a.md': 'A2' } })).toBe(true)
    expect(touchesFiles(skill, { skill_md: 'x', files: {} })).toBe(true)
  })
})

describe('decideAutoApply', () => {
  const base = { sentEditorText: 'e', currentEditorText: 'e', touchesFiles: false, baseMatches: true }
  it('applies a valid, untouched-editor, files-free proposal', () => {
    expect(decideAutoApply({ ...base, proposal: proposal() })).toEqual({ action: 'apply' })
  })
  it('skips invalid first, then files, then edited', () => {
    expect(decideAutoApply({ ...base, proposal: proposal({ valid: false }), touchesFiles: true, currentEditorText: 'z' })).toEqual({ action: 'skip', reason: 'invalid' })
    expect(decideAutoApply({ ...base, proposal: proposal(), touchesFiles: true, currentEditorText: 'z' })).toEqual({ action: 'skip', reason: 'files' })
    expect(decideAutoApply({ ...base, proposal: proposal(), currentEditorText: 'z' })).toEqual({ action: 'skip', reason: 'edited' })
  })
  it('skips a replayed proposal made for a different body (base hash mismatch)', () => {
    expect(decideAutoApply({ ...base, proposal: proposal(), baseMatches: false })).toEqual({ action: 'skip', reason: 'edited' })
  })
})

describe('canUndo (compare-and-swap)', () => {
  const applied = { messageId: 'm', before: 'a', after: 'b', afterEditorText: 'b' }
  it('only while the editor is exactly as applied', () => {
    expect(canUndo(applied, 'b')).toBe(true)
    expect(canUndo(applied, 'b!')).toBe(false)
    expect(canUndo(applied, 'a')).toBe(false)
  })
  it('restores skill files with the body, and refuses if files changed after applying', () => {
    const before = { ...skill, skill_md: 'old', files: { 'a.md': 'A' } }
    const after = { ...skill, skill_md: 'new', files: { 'b.md': 'B' } }
    const rec = { messageId: 'm', before: 'old', after: 'new', afterEditorText: 'new', beforeFiles: before.files, afterFiles: after.files }
    expect(canUndo(rec, 'new', after)).toBe(true)
    expect(restoreApplied(after, rec)).toEqual(before)
    expect(canUndo(rec, 'new', { ...after, files: { 'b.md': 'B2' } })).toBe(false)
  })
})

describe('skipMessage', () => {
  it('has a sentence per reason', () => {
    expect(skipMessage('edited', 0)).toBe('編集中のため反映していません')
    expect(skipMessage('invalid', 2)).toContain('2 件')
    expect(skipMessage('files', 0)).toContain('付属ファイル')
  })
})

describe('draftDiff', () => {
  it('diffs the snapshot against the partial draft and anchors at the last addition', () => {
    const d = draftDiff('a\nb\nc', 'a\nX\nY')
    expect(d?.lines.map((l) => l.op)).toEqual(['same', 'del', 'del', 'add', 'add'])
    expect(d?.anchor).toBe(4)
  })
  it('has no anchor when nothing is added yet, and returns null when too large', () => {
    expect(draftDiff('a\nb', 'a\nb')?.anchor).toBe(-1)
    const big = Array.from({ length: 3000 }, (_, i) => `x${i}`).join('\n')
    const other = Array.from({ length: 3000 }, (_, i) => `y${i}`).join('\n')
    expect(draftDiff(big, other)).toBeNull()
  })
})
