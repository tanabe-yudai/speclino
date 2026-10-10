import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ChatPanel } from './ChatPanel'
import type { ChatDraft } from '@/types/chatDraft'
import type { KnowledgeChatController } from '@/types/knowledgeChat'

const controller = (over: Partial<KnowledgeChatController> = {}): KnowledgeChatController => ({
  chats: [],
  current: null,
  sending: false,
  streaming: null,
  draft: null,
  stop: () => {},
  error: null,
  listError: null,
  select: () => {},
  startNew: () => {},
  send: async () => true,
  remove: async () => {},
  summarize: async () => null,
  bind: async () => null,
  ...over,
})

const render = (chat: KnowledgeChatController) =>
  renderToStaticMarkup(<ChatPanel chat={chat} editorText="" beforeText={() => ''} afterText={() => ''} onApply={() => {}} inline />)

describe('ChatPanel', () => {
  it('keeps the input focusable while sending (read-only, not disabled) and blocks only the send button', () => {
    const html = render(controller({ sending: true, streaming: { userText: '書いて', reply: '' } }))
    const textarea = html.match(/<textarea[^>]*>/)?.[0] ?? ''
    expect(textarea).toContain('readOnly=""')
    expect(textarea).not.toContain('disabled=""')
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>返答を待っています…/)
  })

  it('shows the body being written inside the conversation, under the reply, with a stop button', () => {
    const draft: ChatDraft = { field: 'body', before: '', text: '# 手順\n' }
    const html = render(controller({ sending: true, streaming: { userText: '書いて', reply: '書きます' }, draft }))
    const list = html.slice(html.indexOf('<ol'), html.indexOf('</ol>'))
    expect(list.indexOf('書きます')).toBeGreaterThan(-1)
    expect(list.indexOf('AI が書いている本文')).toBeGreaterThan(list.indexOf('書きます'))
    expect(list).toContain('# 手順')
    expect(list).toMatch(/<button[^>]*>止める<\/button>/)
  })

  it('does not let the browser anchor the page scroll to the growing chat', () => {
    expect(render(controller())).toContain('[overflow-anchor:none]')
  })
})
