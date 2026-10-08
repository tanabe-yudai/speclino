import { describe, expect, it } from 'vitest'
import { checkSkillFrontmatter, checkSkillPath, splitSkillMd } from './skillFrontmatter'

describe('splitSkillMd', () => {
  it('separates frontmatter from the body', () => {
    expect(splitSkillMd('---\r\nname: a\r\ndescription: 説明\r\n---\r\n\r\n# 見出し\r\n本文')).toEqual({
      name: 'a',
      description: '説明',
      body: '# 見出し\n本文',
    })
  })

  it('returns everything as body without a readable frontmatter', () => {
    expect(splitSkillMd('# だけ')).toEqual({ name: null, description: null, body: '# だけ' })
    expect(splitSkillMd('---\nname: a\n本文').body).toBe('---\nname: a\n本文')
  })
})

describe('checkSkillFrontmatter', () => {
  it('reads name and description', () => {
    const r = checkSkillFrontmatter('---\nname: review-api\ndescription: "API を見直す"\n---\n本文')
    expect(r).toEqual({ name: 'review-api', description: 'API を見直す', problems: [] })
  })

  it('reads a folded block description', () => {
    const r = checkSkillFrontmatter('---\nname: a\ndescription: >\n  一行目\n  二行目\n---\n')
    expect(r.description).toBe('一行目 二行目')
    expect(r.problems).toEqual([])
  })

  it('reports a missing frontmatter, name and description', () => {
    expect(checkSkillFrontmatter('# 本文だけ').problems).toEqual([
      'SKILL.md の先頭に --- で囲んだ frontmatter が必要です',
    ])
    expect(checkSkillFrontmatter('---\nname: a\n').problems).toContain('frontmatter が --- で閉じていません')
    const r = checkSkillFrontmatter('---\nfoo: 1\n---\n')
    expect(r.problems).toEqual(['name がありません', 'description がありません'])
  })

  it('checks the name format and the description length', () => {
    expect(checkSkillFrontmatter('---\nname: Review API\ndescription: x\n---\n').problems).toEqual([
      'name は英小文字・数字・ハイフンで 64 文字以内にします',
    ])
    const long = 'あ'.repeat(1025)
    expect(checkSkillFrontmatter(`---\nname: a\ndescription: ${long}\n---\n`).problems).toEqual([
      'description は 1024 文字以内にします',
    ])
  })
})

describe('checkSkillPath', () => {
  it('accepts a normalized relative path', () => {
    expect(checkSkillPath('scripts/run.sh', [])).toBeNull()
  })

  it.each([
    ['', 'パスを入力してください'],
    ['/abs', '相対パスにしてください（/ や C: で始めない）'],
    ['C:/x', '相対パスにしてください（/ や C: で始めない）'],
    ['a\\b', '区切りは / を使ってください'],
    ['../x', '. や .. や空の区切りは使えません'],
    ['a//b', '. や .. や空の区切りは使えません'],
    ['a/', '. や .. や空の区切りは使えません'],
    ['./a', '. や .. や空の区切りは使えません'],
    ['SKILL.md', 'SKILL.md は本文の欄で編集します'],
  ])('rejects %j', (path, message) => {
    expect(checkSkillPath(path, [])).toBe(message)
  })

  it('rejects a case-insensitive duplicate', () => {
    expect(checkSkillPath('Docs/A.md', ['docs/a.md'])).toBe('同じパス（大文字小文字の違いを含む）が既にあります')
  })
})
