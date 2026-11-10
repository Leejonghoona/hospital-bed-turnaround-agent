import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, ApiError, type IpcHome as IH, type User } from '../api'
import { Shell, useToast } from '../parts/Shell'

// 감염관리실 화면: 흐름 docs/화면재설계/flows/감염관리실_이준호.md (출근 → 병상 결정 → 입원 예정 격리 확인 → 접촉자 → 종말소독 → 현황 → 인계)
export function IpcHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('today')
  const [d, setD] = useState<IH | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const load = useCallback(() => api.ipc().then(x => { setD(x); setPick(p => p && x.decisions.some(a => a.exception_id === p) ? p : x.decisions[0]?.exception_id ?? null) }), [])
  useEffect(() => { load(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [load])
  const cur = d?.decisions.find(a => a.exception_id === pick) ?? null
  const s = d?.summary
  return (
    <Shell user={user} nav={[['today', '오늘 할 일'], ['board', '격리 병상'], ['patients', '격리 환자']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        {s && (
          <div className="kpis">
            <button className="kpi" onClick={() => setPage('today')}><span>병상 결정 대기</span><b className={s.decisions ? 'red' : ''}>{s.decisions}</b><em>{s.first_deadline ? `가장 이른 마감 ${s.first_deadline}` : '없음'}</em></button>
            <button className="kpi" onClick={() => setPage('patients')}><span>격리 환자</span><b>{s.isolated}</b><em>오늘 입원 예정 {s.incoming}</em></button>
            <button className="kpi" onClick={() => setPage('board')}><span>빈 격리 병상</span><b>{s.free_np + s.free_single}</b><em>음압 {s.free_np}, 1인실 {s.free_single}</em></button>
            <button className="kpi" onClick={() => setPage('today')}><span>종말소독 대기</span><b className={s.terminal ? 'amber' : ''}>{s.terminal}</b><em>끝나야 다시 배정</em></button>
          </div>
        )}
        {page === 'today' && d && (
          <div className="ipc-today">
            <section className="card span2">
              <h3>늦은 격리 병상 결정 <span className="cnt">{d.decisions.length}</span></h3>
              {d.decisions.length === 0 ? <p className="note">결정할 건이 없습니다</p> : (
                <div className="dec">
                  <table className="sel"><thead><tr><th>마감</th><th>환자</th><th>지금 병상</th><th>균</th></tr></thead>
                    <tbody>{d.decisions.map(a => (
                      <tr key={a.exception_id} className={'row' + (a.exception_id === pick ? ' pick' : '')} onClick={() => setPick(a.exception_id)}>
                        <td className="due">{a.deadline}</td><td className="pt"><b>{a.patient}</b></td><td>{a.bed} {a.room}</td><td className="sub">{a.organism} {a.precautions}</td></tr>))}</tbody></table>
                  {cur && <Decide key={cur.exception_id} a={cur} free={d.free} reasons={d.decline_reasons} onDone={m => { setToast(m); load() }} />}
                </div>)}
            </section>
            <section className="card">
              <h3>오늘 들어올 격리 환자 <span className="cnt">{d.isolated.filter(x => x.status === '입원 예정').length}</span></h3>
              <table><thead><tr><th>환자</th><th>균</th><th>배정 병상</th><th></th></tr></thead>
                <tbody>{d.isolated.filter(x => x.status === '입원 예정').map(x => (
                  <tr key={x.case}><td><b>{x.patient}</b> <span className="sub">{x.age}세 {x.sex}</span></td><td>{x.organism} {x.precautions}</td><td>{x.bed} {x.room}</td>
                    <td className="r">{x.checked ? <span className="tag t-green">확인함</span> : <button className="btn sm" onClick={() => api.ipcCheck(x.case).then(() => { setToast(`${x.patient} 배정을 확인했습니다`); load() })}>확인</button>}</td></tr>))}</tbody></table>
            </section>
            <section className="card">
              <h3>종말소독 대기 <span className="cnt">{d.terminal.length}</span></h3>
              {d.terminal.length === 0 ? <p className="note">없습니다</p> : (
                <table><thead><tr><th>병상</th><th>퇴실</th><th>경과</th><th>진행</th></tr></thead>
                  <tbody>{d.terminal.map(t => <tr key={t.bed}><td>{t.ward} {t.bed}</td><td>{t.departed}</td><td>{t.elapsed}</td><td><span className="tag t-amber">{t.progress}</span></td></tr>)}</tbody></table>)}
            </section>
            <section className="card">
              <h3>동실 접촉자 <span className="cnt">{d.contacts.length}</span></h3>
              {d.contacts.length === 0 ? <p className="note">입실 뒤 격리 오더가 난 다인실이 없습니다</p> : d.contacts.map((c, i) => (
                <p key={i}>{c.time} {c.index} 병실 동실자 {c.count}명: {c.beds.join(', ')}</p>))}
            </section>
            <section className="card">
              <h3>밤사이 기록</h3>
              {d.night.length === 0 ? <p className="note">밤사이 격리 오더와 당직 결정이 없습니다</p> : (
                <div className="tl">{d.night.map((n, i) => <Fragment key={i}><span className="t">{n.time}</span><span className="w">{n.what}, {n.detail}</span></Fragment>)}</div>)}
              <Handover items={d.handover} onDone={m => { setToast(m); load() }} />
            </section>
          </div>
        )}
        {page === 'board' && d && (
          <section className="pk-board ward-board">
            <div className="legend"><span><i className="ok" />빈 병상</span><span><i className="ipc" />격리 환자</span><span><i className="reason" />소독 대기</span><span><i className="no" />사용 중</span></div>
            {d.board.map(w => (
              <div key={w.ward} className="wd"><h3>{w.ward}</h3>
                <div className="rooms">{w.rooms.map(r => (
                  <div key={r.label} className="room"><div className="rh"><b>{r.label}</b><span>{r.grade}</span></div>
                    <div className="bs">{r.beds.map(b => <div key={b.bed} className={'bd k-' + b.kind}><b>{b.bed}</b><span>{b.tag}</span></div>)}</div></div>))}</div>
              </div>))}
          </section>
        )}
        {page === 'patients' && d && (
          <section className="grid log"><div className="tbody"><table>
            <thead><tr><th>상태</th><th>환자</th><th>균</th><th>주의</th><th>병상</th><th>병실</th></tr></thead>
            <tbody>{d.isolated.map(x => <tr key={x.case} className="row"><td><span className="tag t-gray">{x.status}</span></td><td><b>{x.patient}</b> <span className="sub">{x.age}세 {x.sex}</span></td>
              <td>{x.organism}</td><td>{x.precautions}</td><td className="mono">{x.bed || '—'}</td><td>{x.room}</td></tr>)}</tbody></table></div></section>
        )}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </Shell>
  )
}

function Decide({ a, free, reasons, onDone }: { a: IH['decisions'][number]; free: IH['free']; reasons: string[]; onDone: (m: string) => void }) {
  const [mode, setMode] = useState<'move' | 'stay' | null>(null)
  const [reason, setReason] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const run = async (verb: 'approve' | 'decline') => {
    setErr('')
    try {
      const r = await api.ipcDecide(a.exception_id, verb, reason ?? '', memo)
      onDone(verb === 'approve' ? (r.status === 'executed' ? '격리 병상을 다시 찾고 병동과 원무에 알립니다' : '다시 확인한 결과 실행하지 않았습니다') : '지금 병상에서 제자리 격리로 정했습니다')
    } catch (e) { setErr(e instanceof ApiError ? e.message : '처리하지 못했습니다') }
  }
  return (
    <div className="dec-detail">
      <p className="sit">{a.situation}</p>
      <div className="lab">같은 병실 ({a.bed.split('-').slice(0, 2).join('-')}호)</div>
      <div className="mates">{a.roommates.map(m => (
        <span key={m.bed} className={'mate' + (m.risk.length ? ' risk' : '')}><b>{m.bed.split('-')[2]}번</b> {m.sex} {m.age}{m.risk.length ? `, ${m.risk.join(', ')}` : ''}</span>))}</div>
      <div className="lab">비어 있는 격리 병상</div>
      <div className="mates">{free.length ? free.map(f => <span key={f.bed} className="mate free"><b>{f.ward} {f.bed}</b> {f.grade}</span>) : <span className="note">없음</span>}</div>
      <div className="lab">결정</div>
      <div className="seg">
        <button className={mode === 'move' ? 'on' : ''} onClick={() => setMode('move')}>격리 병상으로 다시 찾기</button>
        <button className={mode === 'stay' ? 'on' : ''} onClick={() => setMode('stay')}>지금 병상 유지 등</button>
      </div>
      {mode === 'stay' && (<>
        <div className="seg col">{reasons.map(r => <button key={r} className={reason === r ? 'on' : ''} onClick={() => setReason(r)}>{r}</button>)}</div>
        <div className="note">제자리 격리는 병상 간 1m 이상, 물리적 차단막(질병관리청 2026 관리지침).</div>
        <div className="field"><label htmlFor="dm">병동이 지킬 조치 {reason === '기타' ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
          <textarea id="dm" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="예: 커튼 설치, 개인 보호구, 전용 물품" /></div>
      </>)}
      {err && <div className="err">{err}</div>}
      <button className="btn p full" disabled={!mode || (mode === 'stay' && (!reason || (reason === '기타' && !memo.trim())))}
        onClick={() => run(mode === 'move' ? 'approve' : 'decline')}>결정</button>
    </div>
  )
}

function Handover({ items, onDone }: { items: IH['handover']; onDone: (m: string) => void }) {
  const [t, setT] = useState('')
  return (<>
    <div className="lab">야간 당직 인계</div>
    {items.map((n, i) => <p key={i} className="note">{n.time} {n.who}: {n.text}</p>)}
    <div className="field"><textarea rows={2} maxLength={500} value={t} onChange={e => setT(e.target.value)} placeholder="밤에 이어받을 것" aria-label="인계 메모" /></div>
    <button className="btn sm" disabled={!t.trim()} onClick={() => api.ipcHandover(t).then(() => { setT(''); onDone('인계 메모를 남겼습니다') })}>인계 남기기</button>
  </>)
}
