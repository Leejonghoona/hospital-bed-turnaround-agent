import { Fragment, useEffect, useState } from 'react'
import { api, type AdminSummary, type CaseTrail, type LogRow, type User } from '../api'
import { Shell, useToast } from '../parts/Shell'

// 관리자(의료정보팀) 화면(P4): 흐름과 안전 요약, 접속기록(위변조 확인), 감사용 판단 경과(이름 가림), 잠긴 계정.
export function AdminHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('summary')
  const [sum, setSum] = useState<AdminSummary | null>(null)
  const [log, setLog] = useState<LogRow[]>([])
  const [ok, setOk] = useState<boolean | null>(null)
  const [q, setQ] = useState('')
  const [trail, setTrail] = useState<CaseTrail | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const loadSum = () => api.adminSummary().then(setSum)
  useEffect(() => { loadSum(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [])
  useEffect(() => { if (page === 'log' || page === 'trail') { api.accessLog().then(r => setLog(r.rows)); api.verifyLog().then(r => setOk(r.ok)) } }, [page])
  const rows = log.filter(r => !q || [r.name, r.user_id, r.action, r.case ?? '', r.detail].some(x => x.includes(q)))
  const cases = [...new Set(log.map(r => r.case).filter(Boolean))] as string[]
  return (
    <Shell user={user} nav={[['summary', '오늘 흐름'], ['log', '접속기록'], ['trail', '감사 경과']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{{ summary: '오늘 흐름', log: '접속기록', trail: '감사 경과' }[page]}</h1>
          {page === 'log' && ok !== null && <span className={'tag ' + (ok ? 't-green' : 't-red')}>{ok ? '기록 연결 확인됨' : '기록 위변조 의심'}</span>}</div>
        {page === 'summary' && sum && <p className="sumline">입원 사례 <b>{sum.load['입원 사례']}</b>건, 확정 <b>{sum.load['확정']}</b>건 중 사람 손 없이 <b>{sum.load['사람 손을 안 거친 확정']}</b>건
          <span className="sep" />안전 위반 <b>{sum.safety.reduce((a, x) => a + Number(x['위반'] || 0), 0)}</b>건<span className="sep" />잠긴 계정 <b>{sum.locked.length}</b>개</p>}
        {page === 'summary' && sum && (
          <div className="two-col">
            <section className="card"><h3>흐름</h3><dl className="kv">{Object.entries(sum.flow).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
              <h3>사람 손</h3><dl className="kv">{Object.entries(sum.load).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{typeof v === 'number' && v < 1 ? `${Math.round(v * 100)}%` : String(v)}</dd></Fragment>)}</dl>
              <p className="note">{sum.note}</p></section>
            <section className="card"><h3>안전 검사</h3>
              <table><thead><tr><th>지표</th><th>위반</th><th>검사 수</th></tr></thead>
                <tbody>{sum.safety.map(x => <tr key={x['지표']}><td>{x['지표']}</td><td><span className={'tag ' + (x['위반'] ? 't-red' : 't-green')}>{x['위반']}</span></td><td>{x['검사 수']}</td></tr>)}</tbody></table>
              <h3>잠긴 계정</h3>
              {sum.locked.length ? sum.locked.map(l => (
                <div key={l.user_id} className="lockrow"><span>{l.name} ({l.user_id}) 실패 {l.fails}회</span>
                  <button className="btn sm" onClick={() => api.unlockAccount(l.user_id).then(() => { setToast(`${l.name} 계정 잠금을 풀었습니다`); loadSum() })}>잠금 해제</button></div>
              )) : <p className="note">없습니다</p>}
            </section>
          </div>
        )}
        {page === 'log' && (
          <section className="grid log"><div className="toolbar"><input className="filter" value={q} onChange={e => setQ(e.target.value)} placeholder="이름, 사번, 행위, 환자로 찾기" aria-label="접속기록 찾기" /></div>
            <div className="tbody"><table>
              <colgroup><col style={{ width: 170 }} /><col style={{ width: 110 }} /><col style={{ width: 120 }} /><col style={{ width: 140 }} /><col style={{ width: 110 }} /><col /></colgroup>
              <thead><tr><th>시각(서버)</th><th>병원 시각</th><th>사람</th><th>행위</th><th>환자</th><th>내용, 접속지</th></tr></thead>
              <tbody>{rows.map(r => <tr key={r.hash} className="row"><td className="mono">{r.real_at.replace('T', ' ').slice(0, 19)}</td><td className="mono">{r.sim_at.slice(11)}</td>
                <td>{r.name}</td><td>{r.action}</td><td className="mono">{r.case ?? ''}</td><td className="sub">{r.detail} {r.ip}</td></tr>)}</tbody>
            </table></div></section>
        )}
        {page === 'trail' && (
          <div className="two-col">
            <section className="card"><h3>사례 고르기</h3>
              <p className="note">접속기록에 나온 사례입니다. 진료 정보가 아니라 결정 기록이고 이름은 가립니다.</p>
              <div className="seg col">{cases.slice(0, 20).map(c => <button key={c} onClick={() => api.adminCase(c).then(setTrail)}>{c}</button>)}</div>
            </section>
            <section className="card">{!trail ? <div className="empty">사례를 고르세요</div> : (<>
              <h3>{trail.patient}, {trail.status}, {trail.bed}</h3>
              <div className="tl">{trail.trail.map((t, i) => <Fragment key={i}><span className="t">{t.time}</span><span className="w">{t.what}{t.detail ? `, ${t.detail}` : ''}</span></Fragment>)}</div>
            </>)}</section>
          </div>
        )}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </Shell>
  )
}
