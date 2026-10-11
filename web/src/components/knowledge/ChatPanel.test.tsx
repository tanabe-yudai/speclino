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

  it('shows an editing status (no diff table) under the reply, with a stop button', () => {
    const draft: ChatDraft = { field: 'body', before: '', text: '# 手順\n' }
    const html = render(controller({ sending: true, streaming: { userText: '書いて', reply: '書きます' }, draft }))
    const list = html.slice(html.indexOf('<ol'), html.indexOf('</ol>'))
    expect(list.indexOf('書きます')).toBeGreaterThan(-1)
    expect(list.indexOf('本文を編集中…')).toBeGreaterThan(list.indexOf('書きます'))
    expect(list).toContain('role="status"')
    expect(list).not.toContain('<table')
    expect(list).not.toContain('# 手順')
    expect(list).toMatch(/<button[^>]*>止める<\/button>/)
  })

  it('does not let the browser anchor the page scroll to the growing chat', () => {
    expect(render(controller())).toContain('[overflow-anchor:none]')
  })
})

describe('ChatPanel の調査の表示', () => {
  it('shows what the AI is researching above the editing status, and the thought summary folded', () => {
    const draft: ChatDraft = { field: 'body', before: '', text: '# 手順\n' }
    const streaming = { userText: '書いて', reply: '', thought: '既存の指示を確かめる', research: ['承認済みの知識の一覧を見ています', 'リポジトリ acme/app の src/x.py を読んでいます'] }
    const html = render(controller({ sending: true, streaming }))
    expect(html).toContain('<details')
    expect(html).toContain('考えていること')
    expect(html).toContain('リポジトリ acme/app の src/x.py を読んでいます')
    expect(html).toContain('animate-spin') // 今調べている行
    const writing = render(controller({ sending: true, streaming: { ...streaming, reply: '直します' }, draft }))
    expect(writing.indexOf('本文を編集中…')).toBeGreaterThan(writing.indexOf('src/x.py を読んでいます'))
  })

  it('lists sources under the reply and sandboxes the Google search suggestions', () => {
    const current = {
      id: 'c1',
      workspace: 'w',
      owner_uid: 'u',
      kind: 'instructions' as const,
      artifact_id: null,
      title: 't',
      version: 1,
      created_at: '2026-10-11T00:00:00Z',
      updated_at: '2026-10-11T00:00:00Z',
      messages: [
        {
          id: 'm1',
          role: 'assistant' as const,
          text: '調べました',
          status: 'done' as const,
          created_at: '2026-10-11T00:00:00Z',
          sources: [
            { kind: 'repo' as const, title: 'acme/app src/x.py', ref: 'acme/app@a:src/x.py', url: 'https://github.com/acme/app/blob/a/src/x.py' },
            { kind: 'knowledge' as const, title: '指示書「dev」（改訂 1）', ref: 'instructions:dev@1' },
          ],
          search_suggestions: ['<div class="chip">q</div>'],
        },
      ],
    }
    const html = render(controller({ current }))
    expect(html).toContain('出典')
    expect(html).toContain('href="https://github.com/acme/app/blob/a/src/x.py"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('リポジトリ')
    expect(html).toContain('承認済みの知識')
    const iframe = html.match(/<iframe[^>]*>/)?.[0] ?? ''
    expect(iframe).toContain('sandbox="allow-popups allow-popups-to-escape-sandbox"')
    expect(iframe).not.toContain('allow-scripts')
    expect(iframe).toContain('srcDoc=')
  })
})
