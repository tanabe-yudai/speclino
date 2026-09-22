import type { ScopeBlockProps } from '../types/components'

/** 保証範囲・仮定・未確認範囲を並べる共通ブロック。「何を確認し、何を確認していないか」を明示する。 */
export function ScopeBlock({ title, scopeItems, assumptions, unresolvedItems }: ScopeBlockProps) {
  const hasAny = scopeItems.length > 0 || assumptions.length > 0 || unresolvedItems.length > 0
  return (
    <section className="scope-block">
      <h3>{title}</h3>
      {!hasAny && <p className="muted">記録なし</p>}
      {scopeItems.length > 0 && (
        <div className="scope-row">
          <span className="scope-label">保証範囲</span>
          <ul>
            {scopeItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {assumptions.length > 0 && (
        <div className="scope-row">
          <span className="scope-label">仮定</span>
          <ul>
            {assumptions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {unresolvedItems.length > 0 && (
        <div className="scope-row">
          <span className="scope-label">未確認範囲</span>
          <ul>
            {unresolvedItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
