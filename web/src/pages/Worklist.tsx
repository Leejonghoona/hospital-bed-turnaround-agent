import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, type Patient, type Row, type User, type Worklist as WL } from '../api'
import { Icon, Logo } from '../icons'
import { shiftShort } from '../shifts'

const DAYS = ['일', '월', '화', '수', '목', '금', '토']
// 수속 안내 시각 선택지: 15분 단위 (브라우저 언어와 상관없이 24시간제로 보이게 select로 둔다)
const SLOTS = Array.from({ length: 49 }, (_, i) => `${String(8 + Math.floor(i / 4)).padStart(2, '0')}:${String((i % 4) * 15).padStart(2, '0')}`)
const DECLINE = ['원무가 직접 처리', '환자 사정으로 보류', '병동과 통화로 해결', '기타']

function clock(iso: string) {
  const d = new Date(iso)
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${DAYS[d.getDay()]}요일 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} 기준`
}

function keep(r: Row, tab: string, q: string) {
  const s = !q || r.patient.includes(q) || r.patient_id.includes(q) || r.bed.includes(q)
  return (tab === 'all' || r.kind_label === tab) && s
}

// 처리 영역: 할 일 종류마다 실제로 누르는 처리 하나. 결과는 서버에 사람 기록으로 남는다.
function Action({ r, onDone }: { r: Row; onDone: (msg: string) => void }) {
  const [time, setTime] = useState(r.suggest_at)
  const [choice, setChoice] = useState<string | null>(null)
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (f: () => Promise<string>) => {
    setBusy(true); setErr('')
    try { onDone(await f()) } catch (e) { setErr(e instanceof ApiError ? e.message : '처리하지 못했습니다') } finally { setBusy(false) }
  }

  if (r.kind === 'contact') return (<>
    <div className="field"><label htmlFor="pt">환자에게 안내한 수속 시각</label>
      <select id="pt" value={time} onChange={e => setTime(e.target.value)}>{[...new Set([...SLOTS, r.suggest_at])].filter(Boolean).sort().map(t => <option key={t}>{t}</option>)}</select></div>
    <div className="note">추천 시각은 {r.suggest_at}입니다. 입원 예정 시각과 병상 준비 예상을 함께 본 값입니다.</div>
    {err && <div className="err">{err}</div>}
    <button className="btn p full" disabled={busy || !time} onClick={() => run(async () => { await api.contact(r.case, time); return `${r.patient} 입원 안내를 기록했습니다` })}>연락함으로 기록</button>
  </>)

  if (r.kind === 'task') return (<>
    <div className="seg col">{r.choices.map(c => <button key={c} className={choice === c ? 'on' : ''} onClick={() => setChoice(c)}>{c}</button>)}</div>
    {err && <div className="err">{err}</div>}
    <button className="btn p full" disabled={busy || !choice} onClick={() => run(async () => { await api.taskDone(r.id, choice!); return `${r.patient}: ${choice} 기록했습니다` })}>처리 기록</button>
  </>)

  if (r.kind === 'approval') return declining ? (<>
    <div className="lab">거절 사유</div>
    <div className="seg col">{DECLINE.map(c => <button key={c} className={reason === c ? 'on' : ''} onClick={() => setReason(c)}>{c}</button>)}</div>
    {err && <div className="err">{err}</div>}
    <div className="btns">
      <button className="btn" onClick={() => setDeclining(false)}>취소</button>
      <button className="btn p" disabled={busy || !reason} onClick={() => run(async () => { await api.approval(r.id, 'decline', reason!); return `거절했습니다. 실행하지 않고 원무가 맡습니다` })}>거절 확정</button>
    </div>
  </>) : (<>
    {err && <div className="err">{err}</div>}
    <div className="btns">
      <button className="btn" onClick={() => setDeclining(true)}>거절</button>
      <button className="btn p" disabled={busy} onClick={() => run(async () => {
        const d = await api.approval(r.id, 'approve')
        return d.status === 'executed' ? '승인했습니다. 실행 직전에 다시 확인하고 실행했습니다' : '승인했지만 다시 확인한 결과 실행하지 않았습니다'
      })}>승인</button>
    </div>
  </>)

  return null
}

// 원무 작업 화면: 줄당 한 환자 표 + 오른쪽 상세(사실, 상황, 처리, 경과). 시안 v2 ②.
export function Worklist({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [wl, setWl] = useState<WL | null>(null)
  const [tab, setTab] = useState('all')
  const [q, setQ] = useState('')
  const [pick, setPick] = useState<string | null>(null)
  const [pt, setPt] = useState<Patient | null>(null)
  const [toast, setToast] = useState('')
  const search = useRef<HTMLInputElement>(null)
  const [menu, setMenu] = useState(false)
  const [shift, setShift] = useState<string | null>(null)
  useEffect(() => { api.me().then(m => setShift(m.shift)).catch(() => {}) }, [])

  const load = useCallback(() => api.worklist().then(w => {
    setWl(w); setPick(p => (p && w.rows.some(r => r.id === p)) ? p : w.rows[0]?.id ?? null)
  }), [])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => (wl?.rows ?? []).filter(r => keep(r, tab, q)), [wl, tab, q])
  const cur = rows.find(r => r.id === pick) ?? null
  useEffect(() => { if (cur) api.patient(cur.case).then(setPt).catch(() => setPt(null)); else setPt(null) }, [cur?.case])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 4000); return () => clearTimeout(t) }, [toast])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = document.activeElement instanceof HTMLInputElement
      if (typing || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return
      e.preventDefault()
      const i = rows.findIndex(r => r.id === pick)
      const n = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)))]
      if (n) setPick(n.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows, pick])

  const groups = Object.entries(wl?.groups ?? {})
  const all = wl?.counts.all ?? 0
  const sh = shiftShort(shift)
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <label className="gsearch"><Icon name="search" small />
          <input ref={search} value={q} onChange={e => setQ(e.target.value)} placeholder="환자 이름, 등록번호, 병상 검색" aria-label="검색" /></label>
        <div className="ab-r">
          {wl && <span className="clock">{clock(wl.now)}</span>}
          <div className="menu-wrap">
            <button className="user" onClick={() => setMenu(m => !m)} aria-expanded={menu} aria-haspopup="menu"><span className="av">{user.name[0]}</span>
              <span><div className="n">{user.name}</div><div className="r">{user.role_label}{sh && ` ${sh}`}</div></span><Icon name="chev" small /></button>
            {menu && (
              <div className="menu" role="menu">
                <button role="menuitem" onClick={onSwitch}><Icon name="swap" small />다른 계정으로 로그인</button>
                <button role="menuitem" onClick={onLogout}><Icon name="logout" small />로그아웃</button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="body nosd">
        <main className="main">
          <div className="ph"><h1>처리할 일</h1><span className="meta">{all}건</span></div>
          <div className="tabs" role="tablist">
            {[['all', all] as [string, number], ...groups].map(([g, n]) => (
              <button key={g} role="tab" aria-selected={tab === g} className={'tab' + (tab === g ? ' on' : '')} onClick={() => setTab(g)}>
                {g === 'all' ? '전체' : g} <span className="n">{n}</span></button>
            ))}
          </div>
          <div className="work">
            <section className="grid">
              <div className="tbody">
                {rows.length === 0 ? <div className="empty">처리할 일이 없습니다</div> : (
                  <table>
                    <colgroup><col style={{ width: 108 }} /><col style={{ width: 200 }} /><col style={{ width: 128 }} /><col style={{ width: 76 }} /><col /><col style={{ width: 116 }} /></colgroup>
                    <thead><tr><th>마감</th><th>할 일</th><th>환자</th><th>병상</th><th>근거</th><th>상태</th></tr></thead>
                    <tbody>
                      {rows.map(r => (
                        <tr key={r.id} className={'row' + (r.id === pick ? ' pick' : '')} tabIndex={0} onClick={() => setPick(r.id)}
                          onKeyDown={e => e.key === 'Enter' && setPick(r.id)} aria-selected={r.id === pick}>
                          <td className={'due' + (r.late ? ' late' : '')}>{r.due || '—'}{r.due && r.due_note && <small>{r.due_note}</small>}</td>
                          <td><span className="tag t-gray">{r.kind_label}</span> <span className="sub">{r.action}</span></td>
                          <td className="pt"><b>{r.patient}</b><span className="id">{r.patient_id}</span></td>
                          <td className="mono">{r.bed || '—'}</td>
                          <td className="sub">{r.reason}</td>
                          <td className="st">{r.status}{r.carried && <small>인계받음</small>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
            <aside className="detail" aria-label="상세">
              {!cur || !pt ? <div className="empty">왼쪽에서 할 일을 고르세요</div> : (<>
                <div className="dh">
                  <div className="top"><span className="nm">{pt.patient}</span><span className="id">{pt.patient_id}</span></div>
                  <dl className="facts two">
                    <dt>나이 성별</dt><dd>{pt.age}세 {pt.sex}</dd><dt>진료과</dt><dd>{pt.department}</dd>
                    <dt>입원 예약</dt><dd>{pt.reserved_at}</dd><dt>희망</dt><dd>{pt.room_pref}</dd>
                    <dt>병상</dt><dd>{pt.bed === '-' ? '배정 전' : pt.bed}</dd><dt>격리</dt><dd>{pt.isolation}</dd>
                    <dt>환자</dt><dd>{pt.phone}</dd><dt>보호자</dt><dd>{pt.guardian_phone}</dd>
                  </dl>
                </div>
                <div className="db">
                  {cur.late && <div className="alert"><Icon name="alert" /><div>마감 {cur.due_note === '어제' ? '어제 ' : ''}{cur.due} 지남{cur.status === '책임자 확인 중' ? ', 원무 책임자도 확인 중' : ''}</div></div>}
                  <div className="lab">{cur.action}</div>
                  {cur.situation && <p className="sit">{cur.situation}</p>}
                  {cur.effect && <p className="eff">{cur.effect}</p>}
                  <Action key={cur.id} r={cur} onDone={m => { setToast(m); load() }} />
                  <div className="lab">경과</div>
                  <div className="tl">{pt.timeline.slice().reverse().map((t, i) => (
                    <Fragment key={i}><span className="t">{t.time}</span><span className="w">{t.what}{t.detail ? `, ${t.detail}` : ''}</span></Fragment>
                  ))}</div>
                </div>
              </>)}
            </aside>
          </div>
        </main>
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}
