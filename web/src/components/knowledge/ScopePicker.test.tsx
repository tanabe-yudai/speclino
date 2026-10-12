import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { ProjectSummary } from '@/types/project'
import { ScopePicker } from './ScopePicker'

const projects = [{ id: 'p1', repo_full_name: 'org/shop' }] as ProjectSummary[]

describe('ScopePicker', () => {
  it('ワークスペース全体とプロジェクトの選択を出す（仕様も同じ）', () => {
    const html = renderToStaticMarkup(
      <ScopePicker scope={{ workspace: true, projects: [] }} onChange={() => {}} projects={projects} error={null} />,
    )
    expect(html).toMatch(/type="radio"[^>]*checked=""[^>]*\/>ワークスペース全体/)
    expect(html).toContain('プロジェクトを選ぶ')
    expect(html).not.toContain('org/shop')
  })
  it('プロジェクトを選ぶときは一覧を出し、空なら問題を示す', () => {
    const html = renderToStaticMarkup(
      <ScopePicker scope={{ workspace: false, projects: [] }} onChange={() => {}} projects={projects} error={null} />,
    )
    expect(html).toContain('org/shop')
    expect(html).toContain('ワークスペース全体か、プロジェクトを 1 つ以上選びます')
  })
})
