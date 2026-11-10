import { useEffect, useState } from 'react'
import { api, type Assignment } from '../api'

const STATE_TONE: Record<string, string> = { '입실 대기': 't-blue', '입실 완료': 't-green', '되돌림': 't-gray', '취소': 't-gray' }

// 배정 기록: 에이전트와 사람이 확정한 배정을 최근 순으로. 누가 어떤 근거로 확정했는지, 되돌릴 수 있는지, 지금 상태.
export function AssignmentLog({ onChange }: { onChange: (caseId: string) => void }) {
  const [rows, setRows] = useState<Assignment[] | null>(null)
  const [who, setWho] = useState<'all' | 'agent' | 'human'>('all')
  useEffect(() => { api.assignments().then(r => setRows(r.rows)).catch(() => setRows([])) }, [])
  const list = (rows ?? []).filter(r => who === 'all' || (who === 'agent') === (r.by === '에이전트'))
  const n = (f: (r: Assignment) => boolean) => (rows ?? []).filter(f).length
  return (
    <main className="main">
      <div className="ph"><h1>배정 기록</h1><span className="meta">어제와 오늘 {rows?.length ?? 0}건</span></div>
      <div className="tabs" role="tablist">
        {([['all', '전체', n(() => true)], ['agent', '에이전트 확정', n(r => r.by === '에이전트')], ['human', '사람 확정', n(r => r.by !== '에이전트')]] as const).map(([k, l, c]) => (
          <button key={k} role="tab" aria-selected={who === k} className={'tab' + (who === k ? ' on' : '')} onClick={() => setWho(k)}>{l} <span className="n">{c}</span></button>
        ))}
      </div>
      <section className="grid log">
        <div className="tbody">
          {rows && list.length === 0 ? <div className="empty">배정 기록이 없습니다</div> : (
            <table>
              <colgroup><col style={{ width: 108 }} /><col style={{ width: 150 }} /><col style={{ width: 150 }} /><col style={{ width: 100 }} /><col /><col style={{ width: 120 }} /><col style={{ width: 100 }} /><col style={{ width: 96 }} /></colgroup>
              <thead><tr><th>확정 시각</th><th>환자</th><th>병상</th><th>확정</th><th>근거</th><th>되돌리기</th><th>상태</th><th></th></tr></thead>
              <tbody>{list.map(r => (
                <tr key={r.at + r.case} className="row">
                  <td className="due">{r.time}</td>
                  <td className="pt"><b>{r.patient}</b><span className="id">{r.patient_id}</span></td>
                  <td>{r.bed}</td>
                  <td>{r.by}</td>
                  <td className="sub">{r.basis}{r.runner_up && `, 차순위 ${r.runner_up}`}</td>
                  <td className="sub">{r.undo_open ? `${r.undo_until}까지 가능` : r.undo_until ? `${r.undo_until} 마감` : '—'}</td>
                  <td><span className={'tag ' + (STATE_TONE[r.state] ?? 't-gray')}>{r.state}</span></td>
                  <td>{r.state === '입실 대기' && <button className="btn sm" onClick={() => onChange(r.case)}>병상 변경</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  )
}
