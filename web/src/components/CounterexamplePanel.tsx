import type { CounterexamplePanelProps } from '../types/components'

const KIND_LABELS: Record<'symbolic' | 'observed', string> = {
  symbolic: '記号的（静的解析による反例）',
  observed: '観測（テスト実行による反例）',
}

export function CounterexamplePanel({ counterexample }: CounterexamplePanelProps) {
  const assignments = Object.entries(counterexample.assignments).sort(([a], [b]) => a.localeCompare(b))
  return (
    <div className="counterexample-panel">
      <div className="counterexample-header">
        <span className="counterexample-kind">{KIND_LABELS[counterexample.kind]}</span>
        {counterexample.path_id && <span className="counterexample-path">経路: {counterexample.path_id}</span>}
      </div>
      <p className="counterexample-description">{counterexample.description}</p>
      {assignments.length > 0 && (
        <table className="assignments-table">
          <thead>
            <tr>
              <th>変数</th>
              <th>値</th>
            </tr>
          </thead>
          <tbody>
            {assignments.map(([name, value]) => (
              <tr key={name}>
                <td>
                  <code>{name}</code>
                </td>
                <td>
                  <code>{String(value)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
