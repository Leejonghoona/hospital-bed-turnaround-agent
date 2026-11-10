// 한눈에 보는 숫자 막대: 작은 이름 위에 숫자, 칸은 얇은 선으로 나눈다(큰 카드 대신. Few "Compared to what?"를 위해 보조 문구에 비교 기준)
export type Stat = { label: string; value: number | string; sub?: string; tone?: 'red' | 'amber' }
export function Stats({ items }: { items: Stat[] }) {
  return (
    <div className="stats">{items.map(s => (
      <div key={s.label} className="stat"><span>{s.label}</span><b className={s.tone ?? ''}>{s.value}</b>{s.sub && <em>{s.sub}</em>}</div>
    ))}</div>
  )
}
