import { describe, expect, it } from 'vitest'
import { plainTerms, stripCommands, stripCommandsFromList } from './displayText'

describe('stripCommands', () => {
  it('removes backtick-quoted slash commands and the dangling colon', () => {
    expect(stripCommands('確定するには PR にコメントします: `/speclino attest SHOP-ERR-001:next-action:ab sha256:ff`')).toBe(
      '確定するには PR にコメントします',
    )
  })
  it('removes bare commands', () => {
    expect(stripCommands('次を実行 /speclino attest X:1 sha256:abc して。')).toBe('次を実行 して。')
  })
  it('returns null when nothing readable is left', () => {
    expect(stripCommands('`/speclino attest X sha256:1`')).toBeNull()
  })
  it('leaves ordinary text alone', () => {
    expect(stripCommands('仕様を見直してください。')).toBe('仕様を見直してください。')
  })
  it('drops emptied list items', () => {
    expect(stripCommandsFromList(['確認する', '`/speclino attest a b`'])).toEqual(['確認する'])
  })
})

describe('plainTerms と PR コメントの括弧', () => {
  it('役割名と LLM を画面の言葉にし、コマンドを抜いた後の空の括弧を消す', () => {
    expect(stripCommands('問題が無ければ owner / approver が確定する（PR コメント `/speclino attest X:y sha256:z`）')).toBe(
      '問題が無ければオーナーか承認者が確定する',
    )
    expect(plainTerms('LLM による評価（LLM 3 回中 3 回）')).toBe('AI による評価（AI 3 回中 3 回）')
  })
})

describe('plainTerms の bundle', () => {
  it('bundle 番号を配信版にする', () => {
    expect(plainTerms('期待: bundle 5 → 実装: bundle 4')).toBe('期待: 配信版 5 → 実装: 配信版 4')
    expect(plainTerms('bundle 6')).toBe('配信版 6')
  })
})
