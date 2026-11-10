import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Patient, type Row, type User, type Worklist as WL } from '../api'
import { Icon, Logo } from '../icons'

const TABS = [
  { id: 'all', label: '전체' }, { id: 'late', label: '마감 지남' }, { id: 'approval', label: '승인 요청' },
  { id: 'contact', label: '환자 연락' }, { id: 'ipc_wait', label: '감염관리 결정 대기' }, { id: 'carried', label: '이전 근무' },
]
const KIND_TONE: Record<string, string> = { approval: 't-blue', contact: 't-teal', ipc_wait: 't-violet', confirmed: 't-green', task: 't-red' }
const STATUS_TONE: Record<string, string> = { '책임자 확인': 't-amber', '되돌리기 가능': 't-green' }
const DAYS = ['일', '월', '화', '수', '목', '금', '토']

function clock(iso: string) {
  const d = new Date(iso)
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${DAYS[d.getDay()]}요일 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function keep(r: Row, tab: string, q: string) {
  const t = tab === 'all' || (tab === 'late' && r.late) || (tab === 'carried' && r.carried) || r.kind === tab
  const s = !q || r.patient.includes(q) || r.patient_id.includes(q) || r.bed.includes(q)
  return t && s
}

// 원무 작업 화면: 줄당 한 환자 표 + 오른쪽 상세 패널. 시안 v2 ②. ↑↓ 이동, Enter 열기, / 검색.
const SHIFT_KO: Record<string, string> = { day: '낮 근무 07:00–15:00', evening: '초번 근무 15:00–23:00', night: '밤 근무 23:00–07:00' }

export function Worklist({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [wl, setWl] = useState<WL | null>(null)
  const [tab, setTab] = useState('all')
  const [q, setQ] = useState('')
  const [pick, setPick] = useState<string | null>(null)
  const [pt, setPt] = useState<Patient | null>(null)
  const [dtab, setDtab] = useState<'work' | 'timeline'>('work')
  const search = useRef<HTMLInputElement>(null)
  const [menu, setMenu] = useState(false)
  const [shift, setShift] = useState<string | null>(null)
  useEffect(() => { api.me().then(m => setShift(m.shift)).catch(() => {}) }, [])

  const load = useCallback(() => api.worklist().then(w => { setWl(w); setPick(p => p ?? w.rows[0]?.id ?? null) }), [])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => (wl?.rows ?? []).filter(r => keep(r, tab, q)), [wl, tab, q])
  const cur = rows.find(r => r.id === pick) ?? null
  useEffect(() => { if (cur) api.patient(cur.case).then(setPt).catch(() => setPt(null)); else setPt(null) }, [cur?.case])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement !== search.current) { e.preventDefault(); search.current?.focus(); return }
      if (document.activeElement === search.current) return
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      e.preventDefault()
      const i = rows.findIndex(r => r.id === pick)
      const n = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)))]
      if (n) setPick(n.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows, pick])

  const c = wl?.counts ?? {}
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <label className="gsearch"><Icon name="search" small />
          <input ref={search} value={q} onChange={e => setQ(e.target.value)} placeholder="환자 이름, 등록번호, 병상 검색" aria-label="검색" /><kbd>/</kbd></label>
        <div className="ab-r">
          {wl && <span className="clock">{clock(wl.now)}</span>}
          <button className="iconbtn" aria-label="새로고침" onClick={load}><Icon name="refresh" /></button>
          <div className="menu-wrap">
            <button className="user" onClick={() => setMenu(m => !m)} aria-expanded={menu} aria-haspopup="menu"><span className="av">{user.name[0]}</span>
              <span><div className="n">{user.name}</div><div className="r">{user.role_label}</div></span><Icon name="chev" small /></button>
            {menu && (
              <div className="menu" role="menu">
                <div className="who"><b>{user.name}</b><br />{user.role_label}{shift && <><br />{SHIFT_KO[shift] ?? shift}</>}</div>
                <button role="menuitem" onClick={onSwitch}><Icon name="swap" small />다른 계정으로 로그인</button>
                <div className="hint">같은 단말에서 다음 근무자가 이어서 쓸 때</div>
                <button role="menuitem" onClick={onLogout}><Icon name="back" small />로그아웃</button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="body nosd">
        <main className="main">
          <div className="ph"><h1>오늘 할 일</h1><span className="meta">{c.all ?? 0}건</span>
          </div>
          <div className="tabs" role="tablist">
            {TABS.map(t => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} className={'tab' + (tab === t.id ? ' on' : '')} onClick={() => setTab(t.id)}>
                {t.label} <span className={'n' + (t.id === 'late' && c.late ? ' red' : '')}>{c[t.id] ?? 0}</span></button>
            ))}
          </div>
          <div className="work">
            <section className="grid">
              <div className="toolbar"><span className="btn sm"><Icon name="filter" small />병동 전체<Icon name="chev" small /></span><span className="sp">마감 이른 순</span></div>
              <div className="tbody">
                {rows.length === 0 ? <div className="empty">해당하는 할 일이 없습니다</div> : (
                  <table>
                    <colgroup><col style={{ width: 92 }} /><col style={{ width: 150 }} /><col style={{ width: 140 }} /><col style={{ width: 80 }} /><col /><col style={{ width: 110 }} /></colgroup>
                    <thead><tr><th><span className="nw">마감 <Icon name="sort" small /></span></th><th>할 일</th><th>환자</th><th>병상</th><th>근거</th><th>상태</th></tr></thead>
                    <tbody>
                      {rows.map(r => (
                        <tr key={r.id} className={'row' + (r.id === pick ? ' pick' : '')} tabIndex={0} onClick={() => setPick(r.id)}
                          onKeyDown={e => e.key === 'Enter' && setPick(r.id)} aria-selected={r.id === pick}>
                          <td className={'due' + (r.late ? ' late' : '')}>{r.due || '오늘'}{r.due && r.due_note && <small>{r.due_note}</small>}</td>
                          <td><span className={'tag ' + (KIND_TONE[r.kind] ?? 't-gray')}>{r.kind_label}</span> <span className="sub">{r.action}</span></td>
                          <td className="pt"><b>{r.patient}</b><span className="id">{r.patient_id}</span></td>
                          <td className="mono">{r.bed || '—'}</td>
                          <td className="sub" title={r.reason}>{r.reason}</td>
                          <td><span className={'tag ' + (STATUS_TONE[r.status] ?? 't-gray')}>{r.status}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div className="gfoot"><span><kbd>↑</kbd><kbd>↓</kbd>이동</span><span><kbd>Enter</kbd>열기</span><span><kbd>/</kbd>검색</span>
                <span style={{ marginLeft: 'auto' }}>{rows.length} / {c.all ?? 0}건</span></div>
            </section>
            <aside className="detail" aria-label="상세">
              {!cur || !pt ? <div className="empty">왼쪽에서 할 일을 고르세요</div> : (<>
                <div className="dh">
                  <div className="top"><span className="nm">{pt.patient}</span>{cur.late && <span className="tag t-red">마감 지남</span>}</div>
                  <dl className="facts">
                    <dt>등록번호</dt><dd>{pt.patient_id}</dd>
                    <dt>나이 성별</dt><dd>{pt.age}세 {pt.sex}</dd>
                    <dt>진료과</dt><dd>{pt.department}</dd>
                    <dt>병상</dt><dd>{pt.bed === '-' ? '배정 전' : pt.bed}</dd>
                  </dl>
                </div>
                <div className="dtabs">
                  <button className={'tab' + (dtab === 'work' ? ' on' : '')} onClick={() => setDtab('work')}>처리</button>
                  <button className={'tab' + (dtab === 'timeline' ? ' on' : '')} onClick={() => setDtab('timeline')}>경과</button>
                </div>
                <div className="db">
                  {dtab === 'work' ? (<>
                    {cur.late && <div className="alert"><Icon name="alert" /><div>마감 {cur.due}{cur.due_note ? ` (${cur.due_note})` : ''}이 지났습니다{cur.status === '책임자 확인' ? '. 원무 책임자에게도 알렸습니다' : ''}</div></div>}
                    <div className="lab">다음에 할 일</div>
                    <div className="next"><b>{cur.action}</b><span>{cur.reason}</span></div>
                    <div className="lab">경과</div>
                    <div className="tl">{pt.timeline.slice(-4).reverse().map((t, i) => (
                      <Fragment key={i}><span className="t">{t.time}</span><span className="w">{t.what}</span></Fragment>
                    ))}</div>
                  </>) : (
                    <div className="tl">{pt.timeline.slice().reverse().map((t, i) => (
                      <Fragment key={i}><span className="t">{t.time}</span><span className="w">{t.what}{t.detail ? `, ${t.detail}` : ''}</span></Fragment>
                    ))}</div>
                  )}
                </div>
              </>)}
            </aside>
          </div>
        </main>
      </div>
    </div>
  )
}
