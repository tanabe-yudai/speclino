import { useEffect } from 'react'
import { documentTitle } from '@/services/documentTitle'

/** 画面のタイトルをタブに出す。画面を離れたら SpecLino に戻す。 */
export function useDocumentTitle(page: string | undefined) {
  useEffect(() => {
    document.title = documentTitle(page)
    return () => {
      document.title = documentTitle(undefined)
    }
  }, [page])
}
