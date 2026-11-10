import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, ApiError, type IpcHome as IH, type User } from '../api'
import { Shell, useToast } from '../parts/Shell'

// 감염관리실 화면: 흐름 docs/화면재설계/flows/감염관리실_이준호.md (출근 → 병상 결정 → 입원 예정 격리 확인 → 접촉자 → 종말소독 → 현황 → 인계)
export function IpcHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('patients')
  const [d, setD] = useState<IH | null>(null)
  const [f, setF] = useState('all')
  const [pick, setPick] = useState<string | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const load = useCallback(() => api.ipc().then(x => {
    setD(x)
    const first = x.isolated.find(p => x.decisions.some(dd => dd.case === p.case)) ?? x.isolated[0]
    setPick(p => p && x.isolated.some(i => i.case === p) ? p : first?.case ?? null)
  }), [])
  useEffect(() => { load(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [load])
  const s = d?.summary
  const dec = (c: string) => d?.decisions.find(x => x.case === c)
  const badges = (p: IH['isolated'][number]) => {
    const out: { t: string; late?: boolean }[] = []
    const x = dec(p.case)
    if (x) out.push({ t: `병상 결정, 마감 ${x.deadline}`, late: true })
    if (p.status === '입원 예정' && !p.checked) out.push({ t: '배정 확인 전' })
    return out
  }
  const rows = (d?.isolated ?? []).map(p => ({ ...p, b: badges(p) }))
    .filter(p => f === 'all' || p.status === f).sort((a, b) => b.b.length - a.b.length)
  const cur = rows.find(p => p.case === pick) ?? null
  const count = (k: string) => (d?.isolated ?? []).filter(p => k === 'all' || p.status === k).length
  return (
    <Shell user={user} nav={[['patients', '격리 환자'], ['board', '격리 병상'], ['night', '밤사이·인계']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{{ patients: '격리 환자', board: '격리 병상', night: '밤사이·인계' }[page]}</h1></div>
        {s && <p className="sumline">격리 환자 <b>{s.isolated}</b>명(오늘 입원 예정 {s.incoming})<span className="sep" />빈 격리 병상 <b>{s.free_np + s.free_single}</b>개(음압 {s.free_np}, 1인실 {s.free_single})<span className="sep" />종말소독 대기 <b>{s.terminal}</b>개
          {s.decisions > 0 && <><span className="sep" /><span className="hl">병상 결정 {s.decisions}건, 마감 {s.first_deadline}</span></>}</p>}
        {page === 'patients' && d && (<>
          <div className="tabs" role="tablist">{(['all', '병상 대기', '입원 예정', '재원', '퇴원 진행'] as const).map(k => (
            <button key={k} role="tab" aria-selected={f === k} className={'tab' + (f === k ? ' on' : '')} onClick={() => setF(k)}>{k === 'all' ? '전체' : k} <span className="n">{count(k)}</span></button>))}</div>
          <div className="work">
            <section className="grid"><div className="tbody">
              {rows.length === 0 ? <div className="empty">해당하는 격리 환자가 없습니다</div> : (
                <table>
                  <colgroup><col style={{ width: 92 }} /><col style={{ width: 140 }} /><col style={{ width: 90 }} /><col style={{ width: 90 }} /><col style={{ width: 120 }} /><col /></colgroup>
                  <thead><tr><th>상태</th><th>환자</th><th>균</th><th>주의</th><th>병상</th><th>확인 필요</th></tr></thead>
                  <tbody>{rows.map(p => (
                    <tr key={p.case} className={'row' + (p.case === pick ? ' pick' : '')} onClick={() => setPick(p.case)} tabIndex={0}>
                      <td><span className="tag t-gray">{p.status}</span></td>
                      <td className="pt"><b>{p.patient}</b><span className="id">{p.age}세 {p.sex}</span></td>
                      <td>{p.organism}</td><td className="sub">{p.precautions}</td>
                      <td>{p.bed ? <span className="bedst"><b className="mono">{p.bed}</b><small>{p.room}</small></span> : <span className="sub">—</span>}</td>
                      <td>{p.b.map(x => <span key={x.t} className={'badge' + (x.late ? ' late' : '')}>{x.t}</span>)}</td>
                    </tr>))}</tbody>
                </table>)}
            </div></section>
            <aside className="detail" aria-label="상세">
              {!cur ? <div className="empty">왼쪽에서 환자를 고르세요</div> : (<>
                <div className="dh"><div className="top"><span className="nm">{cur.patient}</span><span className="id">{cur.age}세 {cur.sex}</span></div>
                  <dl className="facts two"><dt>상태</dt><dd>{cur.status}</dd><dt>균</dt><dd>{cur.organism}</dd>
                    <dt>주의</dt><dd>{cur.precautions}</dd><dt>병상</dt><dd>{cur.bed ? `${cur.bed} ${cur.room}` : '배정 전'}</dd></dl></div>
                <div className="db">
                  {dec(cur.case) && <div className="issue"><div className="lab">늦은 격리 병상 결정<span className="badge late">마감 {dec(cur.case)!.deadline}</span></div>
                    <Decide key={cur.case} a={dec(cur.case)!} free={d.free} reasons={d.decline_reasons} onDone={m => { setToast(m); load() }} /></div>}
                  {cur.status === '입원 예정' && (
                    <div className="issue routine"><div className="lab">배정 병상 확인</div>
                      <p className="sit">에이전트가 규칙대로 {cur.room || '병상'}에 배정했습니다. 문제가 없으면 확인을 남깁니다.</p>
                      {cur.checked ? <span className="tag t-green">확인함</span> : <button className="btn p" onClick={() => api.ipcCheck(cur.case).then(() => { setToast(`${cur.patient} 배정을 확인했습니다`); load() })}>배정 확인</button>}
                    </div>)}
                  {!dec(cur.case) && cur.status !== '입원 예정' && <p className="note">처리할 것이 없습니다. 격리 해제는 주치의나 감염관리실 오더로 EMR에서 합니다.</p>}
                </div>
              </>)}
            </aside>
          </div>
        </>)}
        {page === 'board' && d && (
          <section className="pk-board ward-board">
            <div className="legend"><span><i className="ok" />빈 병상</span><span><i className="ipc" />격리 환자</span><span><i className="reason" />소독 대기</span><span><i className="no" />사용 중</span></div>
            {d.terminal.length > 0 && <p className="note">종말소독 대기: {d.terminal.map(t => `${t.ward} ${t.bed}(퇴실 ${t.departed}, ${t.elapsed} 지남, ${t.progress})`).join(' / ')}</p>}
            {d.board.map(w => (
              <div key={w.ward} className="wd"><h3>{w.ward}</h3>
                <div className="rooms">{w.rooms.map(r => (
                  <div key={r.label} className="room"><div className="rh"><b>{r.label}</b><span>{r.grade}</span></div>
                    <div className="bs">{r.beds.map(b => <div key={b.bed} className={'bd k-' + b.kind}><b>{b.bed}</b><span>{b.tag}</span></div>)}</div></div>))}</div>
              </div>))}
          </section>
        )}
        {page === 'night' && d && (
          <div className="two-col">
            <section className="card"><h3>밤사이 기록</h3>
              {d.night.length === 0 ? <p className="note">밤사이 격리 오더와 당직 결정이 없습니다</p> : (
                <div className="tl">{d.night.map((n, i) => <Fragment key={i}><span className="t">{n.time}</span><span className="w">{n.what}, {n.detail}</span></Fragment>)}</div>)}
              <h3>동실 접촉자</h3>
              {d.contacts.length === 0 ? <p className="note">입실 뒤 격리 오더가 난 다인실이 없습니다</p> : d.contacts.map((c, i) => <p key={i}>{c.time} {c.index} 병실 동실자 {c.count}명: {c.beds.join(', ')}</p>)}
            </section>
            <section className="card"><Handover items={d.handover} onDone={m => { setToast(m); load() }} /></section>
          </div>
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
