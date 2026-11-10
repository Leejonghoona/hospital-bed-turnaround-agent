import { Fragment, useEffect, useState } from 'react'
import { api, type AdminSummary, type AiOps, type CaseTrail, type LogRow, type User } from '../api'
import { Shell, useToast } from '../parts/Shell'
import { AiOpsPage } from './AiOps'

// 의료정보팀 화면: AI 운영(에이전트 성과, 막은 판단, 자율 범위, 모델과 평가, 새 병원 설정), 접속기록(위변조 확인), 감사 경과(이름 가림), 계정.
// 오늘 흐름은 원무팀장 화면으로 옮겼다(DECISIONS 2026-11-10).
export function AdminHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('ai')
  const [ops, setOps] = useState<AiOps | null>(null)
  const [sum, setSum] = useState<AdminSummary | null>(null)
  const [log, setLog] = useState<LogRow[]>([])
  const [ok, setOk] = useState<boolean | null>(null)
  const [q, setQ] = useState('')
  const [trail, setTrail] = useState<CaseTrail | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const loadSum = () => api.adminSummary().then(setSum)
  useEffect(() => { loadSum(); api.aiOps().then(setOps); api.me().then(m => setNow(m.now)).catch(() => {}) }, [])
  useEffect(() => { if (page === 'log' || page === 'trail') { api.accessLog().then(r => setLog(r.rows)); api.verifyLog().then(r => setOk(r.ok)) } }, [page])
  const rows = log.filter(r => !q || [r.name, r.user_id, r.action, r.case ?? '', r.detail].some(x => x.includes(q)))
  const cases = [...new Set(log.map(r => r.case).filter(Boolean))] as string[]
  return (
    <Shell user={user} nav={[['ai', 'AI 운영'], ['log', '접속기록'], ['trail', '감사 경과'], ['acct', '계정']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{{ ai: 'AI 운영', log: '접속기록', trail: '감사 경과', acct: '계정' }[page]}</h1>
          {page === 'log' && ok !== null && <span className={'tag ' + (ok ? 't-green' : 't-red')}>{ok ? '기록 연결 확인됨' : '기록 위변조 의심'}</span>}</div>
        {page === 'ai' && (ops ? <AiOpsPage d={ops} onChange={setOps} toast={setToast} /> : <div className="empty">불러오는 중</div>)}
        {page === 'acct' && sum && (
          <section className="card acct"><h3>잠긴 계정</h3>
            <p className="note">비밀번호를 5번 틀리면 잠기고, 의료정보팀이 풀 때까지 잠겨 있습니다.</p>
            {sum.locked.length ? sum.locked.map(l => (
              <div key={l.user_id} className="lockrow"><span>{l.name} ({l.user_id}) 실패 {l.fails}회</span>
                <button className="btn sm" onClick={() => api.unlockAccount(l.user_id).then(() => { setToast(`${l.name} 계정 잠금을 풀었습니다`); loadSum() })}>잠금 해제</button></div>
            )) : <p>잠긴 계정이 없습니다</p>}
          </section>
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
