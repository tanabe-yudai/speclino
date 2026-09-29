import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { listRuns } from '@/api/reviewRuns'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { RunRow } from '@/components/app/RunRow'
import { LoadingState } from '@/components/LoadingState'
import type { ReviewRunSummary } from '@/types/run'

/** プロジェクト画面（S45、段 2）ができるまでの仮の画面。そのリポジトリの検査だけを出す。 */
export function ProjectPlaceholder() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>()
  const fullName = `${owner}/${repo}`
  const [runs, setRuns] = useState<ReviewRunSummary[] | null>(null)

  useEffect(() => {
    let alive = true
    listRuns(50)
      .then((all) => alive && setRuns(all.filter((r) => r.repo_full_name === fullName)))
      .catch(() => alive && setRuns([]))
    return () => {
      alive = false
    }
  }, [fullName])

  return (
    <div className="space-y-6">
      <header>
        <p className="font-mono text-xs text-ink-3">{fullName}</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">{repo}</h1>
      </header>
      <Panel>
        <PanelTitle>このプロジェクトの画面は準備中です</PanelTitle>
        <p className="mt-1 text-sm text-ink-2">仕様・要求・規約の状態はここにまとまる予定です。今は検査の履歴だけを出しています。</p>
      </Panel>
      {runs === null ? (
        <LoadingState />
      ) : runs.length === 0 ? (
        <p className="text-sm text-ink-3">まだ検査がありません。</p>
      ) : (
        <div className="space-y-2">
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </div>
      )}
    </div>
  )
}
