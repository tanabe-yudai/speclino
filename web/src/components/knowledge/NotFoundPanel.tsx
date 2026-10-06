import { Link } from 'react-router-dom'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { paths } from '@/services/paths'

export function NotFoundPanel({ title }: { title: string }) {
  return (
    <Panel className="mx-auto max-w-xl text-center">
      <PanelTitle className="text-base">{title}</PanelTitle>
      <Link className="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline" to={paths.home()}>
        ホームへ戻る
      </Link>
    </Panel>
  )
}
