/**
 * SKILL.md の frontmatter（Agent Skills の name / description）と付属ファイルのパスの検査。
 * 画面で早く気付かせるためのもので、正はサーバの検証（domain/knowledge.py）。
 */

export interface SkillFrontmatter {
  name: string | null
  description: string | null
  problems: string[]
}

const NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/
const DESCRIPTION_MAX = 1024

function unquote(v: string): string {
  const t = v.trim()
  if (t.length >= 2 && ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'")))) {
    return t.slice(1, -1)
  }
  return t
}

/** `key: value` と、`key: >` / `key: |` の字下げしたブロックだけを読む（YAML の全体は扱わない）。 */
function readFields(lines: string[]): Map<string, string> {
  const fields = new Map<string, string>()
  for (let i = 0; i < lines.length; i++) {
    const m = /^([A-Za-z_][\w-]*):(.*)$/.exec(lines[i])
    if (!m) continue
    const [, key, rest] = m
    const marker = rest.trim()
    if (marker === '>' || marker === '|' || marker === '>-' || marker === '|-') {
      const block: string[] = []
      while (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1])) block.push(lines[++i].trim())
      fields.set(key, block.join(marker.startsWith('>') ? ' ' : '\n'))
    } else {
      fields.set(key, unquote(rest))
    }
  }
  return fields
}

export function checkSkillFrontmatter(skillMd: string): SkillFrontmatter {
  const lines = skillMd.replace(/\r\n?/g, '\n').split('\n')
  if (lines[0]?.trim() !== '---') {
    return { name: null, description: null, problems: ['SKILL.md の先頭に --- で囲んだ frontmatter が必要です'] }
  }
  const end = lines.findIndex((l, i) => i > 0 && l.trim() === '---')
  if (end < 0) return { name: null, description: null, problems: ['frontmatter が --- で閉じていません'] }
  const fields = readFields(lines.slice(1, end))
  const name = fields.get('name') || null
  const description = fields.get('description') || null
  const problems: string[] = []
  if (!name) problems.push('name がありません')
  else if (!NAME_RE.test(name) || name.length > 64) problems.push('name は英小文字・数字・ハイフンで 64 文字以内にします')
  if (!description) problems.push('description がありません')
  else if ([...description].length > DESCRIPTION_MAX) problems.push('description は 1024 文字以内にします')
  return { name, description, problems }
}

/** 付属ファイルの相対パスの検査。others は他のファイルのパス（大文字小文字違いの重複も拒否）。 */
export function checkSkillPath(path: string, others: string[]): string | null {
  if (!path) return 'パスを入力してください'
  if (path.includes('\\')) return '区切りは / を使ってください'
  if (path.includes('\0')) return '使えない文字が含まれています'
  if (path.startsWith('/') || /^[A-Za-z]:/.test(path)) return '相対パスにしてください（/ や C: で始めない）'
  if (path.split('/').some((seg) => seg === '' || seg === '.' || seg === '..')) return '. や .. や空の区切りは使えません'
  if (path.toLowerCase() === 'skill.md') return 'SKILL.md は本文の欄で編集します'
  if (others.some((o) => o.toLowerCase() === path.toLowerCase())) return '同じパス（大文字小文字の違いを含む）が既にあります'
  return null
}
