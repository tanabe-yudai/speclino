import { describe, expect, it } from 'vitest'
import { agentCardUrl } from './a2a'

describe('agentCardUrl', () => {
  it('origin に well-known パスを付ける', () => {
    expect(agentCardUrl('https://speclino.example.com')).toBe(
      'https://speclino.example.com/.well-known/agent-card.json',
    )
  })

  it('origin 末尾のスラッシュは重ねない', () => {
    expect(agentCardUrl('http://localhost:5173/')).toBe('http://localhost:5173/.well-known/agent-card.json')
  })
})
