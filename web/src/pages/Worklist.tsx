import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, type Patient, type Row, type User, type Worklist as WL } from '../api'
import { Icon, Logo } from '../icons'
import { lockNow } from '../parts/AutoLock'
import { shiftShort } from '../shifts'
import { AssignmentLog } from './AssignmentLog'
import { BedPicker } from './BedPicker'
import { UndoForm } from '../parts/Undo'
import { TodayAdmissions } from './TodayAdmissions'

const DAYS = ['일', '월', '화', '수', '목', '금', '토']
// 수속 안내 시각 선택지: 15분 단위 (브라우저 언어와 상관없이 24시간제로 보이게 select로 둔다)
// 입원 수속 창구 운영 시간 안에서만 고른다(강남세브란스 "오후 5시 30분 이후에는 응급실원무 창구"). 09:00~17:30
const SLOTS = Array.from({ length: 35 }, (_, i) => `${String(9 + Math.floor(i / 4)).padStart(2, '0')}:${String((i % 4) * 15).padStart(2, '0')}`)
const DECLINE = ['원무가 직접 처리', '환자 사정으로 보류', '병동과 통화로 해결', '기타']

function clock(d: Date) {
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${DAYS[d.getDay()]}) ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 현재 시각: 합성 병원의 시각에서 시작해 실제로 흐른 시간만큼 간다 (데이터는 합성 병원 시각 기준이라 실제 시계를 그대로 쓰지 않는다)
function useClock(start: string | undefined) {
  const [base] = useState(() => Date.now())
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 15000); return () => clearInterval(t) }, [])
  return start ? new Date(new Date(start).getTime() + (Date.now() - base)) : null
}

function dateKo(iso: string) {
  const d = new Date(iso)
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 기한까지 남은 시간 또는 넘긴 시간 (분 단위 내림)
function overdue(iso: string, now: string) {
  const m = Math.floor((new Date(now).getTime() - new Date(iso).getTime()) / 60000)
  const a = Math.abs(m), h = Math.floor(a / 60), r = a % 60
  const t = h ? (r ? `${h}시간 ${r}분` : `${h}시간`) : `${r}분`
  return m > 0 ? `${t} 초과` : `${t} 남음`
}

function keep(r: Row, tab: string, q: string) {
  const s = !q || r.patient.includes(q) || r.patient_id.includes(q) || r.bed.includes(q)
  return (tab === 'all' || r.kind_label === tab) && s
}

// 메모(특이사항): 고르는 처리 옆에 적는 선택 입력. '기타'를 고르면 반드시 적는다.
function Memo({ v, set, need }: { v: string; set: (s: string) => void; need: boolean }) {
  return (
    <div className="field"><label htmlFor="memo">메모 {need ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
      <textarea id="memo" rows={2} maxLength={500} value={v} onChange={e => set(e.target.value)}
        placeholder="통화 내용, 환자 요청 등" /></div>
  )
}

// 처리 영역: 할 일 종류마다 실제로 누르는 처리 하나. 결과는 서버에 사람 기록으로 남는다.
export function Action({ r, onDone }: { r: Row; onDone: (msg: string) => void }) {
  const [time, setTime] = useState(r.suggest_at)
  const [outcome, setOutcome] = useState<'done' | 'no_answer' | 'postpone' | 'cancel'>('done')
  const [choice, setChoice] = useState<string | null>(null)
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (f: () => Promise<string>) => {
    setBusy(true); setErr('')
    try { onDone(await f()) } catch (e) { setErr(e instanceof ApiError ? e.message : '처리하지 못했습니다') } finally { setBusy(false) }
  }
  const other = (choice ?? reason) === '기타'
  const memoOk = !other || memo.trim().length > 0

  if (r.kind === 'contact') return (<>
    <div className="seg">
      <button className={outcome === 'done' ? 'on' : ''} onClick={() => setOutcome('done')}>통화함</button>
      <button className={outcome === 'no_answer' ? 'on' : ''} onClick={() => setOutcome('no_answer')}>연락 안 됨</button>
      <button className={outcome === 'postpone' ? 'on' : ''} onClick={() => setOutcome('postpone')}>입원 연기</button>
      <button className={outcome === 'cancel' ? 'on' : ''} onClick={() => setOutcome('cancel')}>입원 취소</button>
    </div>
    {outcome === 'done' && (<>
      <div className="field"><label htmlFor="pt">환자에게 안내한 수속 시각</label>
        <select id="pt" value={time} onChange={e => setTime(e.target.value)}>{[...new Set([...SLOTS, r.suggest_at])].filter(Boolean).sort().map(t => <option key={t}>{t}</option>)}</select></div>
      <div className="note">추천 시각은 {r.suggest_at}입니다. 입원 예정 시각과 병상 준비 예상을 함께 본 값입니다.</div>
    </>)}
    <Memo v={memo} set={setMemo} need={outcome === 'postpone' || outcome === 'cancel'} />
    {err && <div className="err">{err}</div>}
    <button className="btn p full" disabled={busy || (outcome === 'done' && !time) || ((outcome === 'postpone' || outcome === 'cancel') && !memo.trim())} onClick={() => run(async () => {
      await api.contact(r.case, outcome, time, memo)
      return { done: `${r.patient} 입원 안내를 기록했습니다`, no_answer: `${r.patient} 연락 안 됨을 기록했습니다. 할 일은 남겨 둡니다`,
        postpone: `${r.patient} 입원 연기를 기록했습니다. 병상을 풀었습니다`, cancel: `${r.patient} 입원 취소를 기록했습니다. 병상을 풀었습니다` }[outcome]
    })}>{{ done: '연락함으로 기록', no_answer: '연락 안 됨 기록', postpone: '입원 연기 기록', cancel: '입원 취소 기록' }[outcome]}</button>
  </>)

  if (r.kind === 'task') return (<>
    <div className="choices">{r.choices.map(c => (
      <button key={c.label} className={choice === c.label ? 'on' : ''} onClick={() => setChoice(c.label)}>
        <b>{c.label}</b><span>{c.desc}</span></button>
    ))}</div>
    <Memo v={memo} set={setMemo} need={other} />
    {err && <div className="err">{err}</div>}
    <button className="btn p full" disabled={busy || !choice || !memoOk} onClick={() => run(async () => { await api.taskDone(r.id, choice!, memo); return `${r.patient}: ${choice} 기록했습니다` })}>처리 기록</button>
  </>)

  if (r.kind === 'approval') return declining ? (<>
    <div className="lab">거절 사유</div>
    <div className="seg col">{DECLINE.map(c => <button key={c} className={reason === c ? 'on' : ''} onClick={() => setReason(c)}>{c}</button>)}</div>
    <Memo v={memo} set={setMemo} need={other} />
    {err && <div className="err">{err}</div>}
    <div className="btns">
      <button className="btn" onClick={() => setDeclining(false)}>취소</button>
      <button className="btn p" disabled={busy || !reason || !memoOk} onClick={() => run(async () => { await api.approval(r.id, 'decline', reason!, memo); return `거절했습니다. 실행하지 않고 원무가 맡습니다` })}>거절 확정</button>
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

  if (r.kind === 'confirmed') return <UndoForm caseId={r.case} until={r.due} onDone={onDone} />

  return null
}

// 정렬: 머리글을 누르면 그 칸 기준으로, 한 번 더 누르면 반대로. 마감은 서버 순서(마감 지난 일 먼저, 이른 순)가 기본.
type SortKey = 'due' | 'patient' | 'bed' | 'status'
const SORT_LABEL: Record<SortKey, string> = { due: '마감', patient: '환자', bed: '병상', status: '상태' }
function sortRows(rows: Row[], order: Map<string, number>, key: SortKey, dir: 1 | -1) {
  const cmp = (a: Row, b: Row) => key === 'due' ? (order.get(a.id)! - order.get(b.id)!)
    : key === 'patient' ? a.patient.localeCompare(b.patient, 'ko')
      : key === 'bed' ? (a.bed || '~').localeCompare(b.bed || '~') : a.status.localeCompare(b.status, 'ko')
  return [...rows].sort((a, b) => dir * cmp(a, b) || order.get(a.id)! - order.get(b.id)!)
}

// 원무 작업 화면: 줄당 한 환자 표 + 오른쪽 상세(사실, 상황, 처리, 경과). 시안 v2 ②.
export function Worklist({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [wl, setWl] = useState<WL | null>(null)
  const [tab, setTab] = useState('all')
  const [q, setQ] = useState('')
  const [pick, setPick] = useState<string | null>(null)
  const [pt, setPt] = useState<Patient | null>(null)
  const [toast, setToast] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'due', dir: 1 })
  const search = useRef<HTMLInputElement>(null)
  const [menu, setMenu] = useState(false)
  const [page, setPage] = useState<'work' | 'log'>('work')
  const [picker, setPicker] = useState<string | null>(null)
  const [rev, setRev] = useState(0)
  const [shift, setShift] = useState<string | null>(null)
  const [meNow, setMeNow] = useState<string | undefined>()
  useEffect(() => { api.me().then(m => { setShift(m.shift); setMeNow(m.now) }).catch(() => {}) }, [])

  const load = useCallback(() => api.worklist().then(w => {
    setWl(w); setPick(p => (p && w.rows.some(r => r.id === p)) ? p : w.rows[0]?.id ?? null)
  }), [])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => {
    const order = new Map((wl?.rows ?? []).map((r, i) => [r.id, i]))
    return sortRows((wl?.rows ?? []).filter(r => keep(r, tab, q)), order, sort.key, sort.dir)
  }, [wl, tab, q, sort])
  const th = (k: SortKey) => (
    <th aria-sort={sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className={'sortbtn' + (sort.key === k ? ' on' : '')} onClick={() => setSort(p => ({ key: k, dir: p.key === k ? (p.dir === 1 ? -1 : 1) : 1 }))}>
        {SORT_LABEL[k]}<span className="arrow">{sort.key === k ? (sort.dir === 1 ? '↑' : '↓') : '↕'}</span></button></th>)
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

  const now = useClock(meNow)
  const groups = Object.entries(wl?.groups ?? {})
  const all = wl?.counts.all ?? 0
  const sh = shiftShort(shift)
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <nav className="gnav">
          <button className={page === 'work' ? 'on' : ''} aria-current={page === 'work' ? 'page' : undefined} onClick={() => setPage('work')}>오늘 입원</button>
          <button className={page === 'log' ? 'on' : ''} aria-current={page === 'log' ? 'page' : undefined} onClick={() => setPage('log')}>배정 기록</button>
        </nav>
        <label className="gsearch"><Icon name="search" small />
          <input ref={search} value={q} onChange={e => setQ(e.target.value)} placeholder="환자 이름, 등록번호, 병상 검색" aria-label="검색" /></label>
        <div className="ab-r">
          {now && <span className="clock">{clock(now)}</span>}
          <div className="menu-wrap">
            <button className="user" onClick={() => setMenu(m => !m)} aria-expanded={menu} aria-haspopup="menu"><span className="av">{user.name[0]}</span>
              <span><div className="n">{user.name}</div><div className="r">{user.role_label}</div></span><Icon name="chev" small /></button>
            {menu && (
              <div className="menu" role="menu">
                <div className="who"><b>{user.name}</b><br />{user.role_label}{sh && <><br />{sh}</>}</div>
                <button role="menuitem" onClick={onSwitch}><Icon name="swap" small />다른 계정으로 로그인</button>
                <button role="menuitem" onClick={() => { setMenu(false); lockNow() }}><Icon name="lock" small />화면 잠금</button>
                <button role="menuitem" onClick={onLogout}><Icon name="logout" small />로그아웃</button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="body nosd">
        {page === 'log' ? <AssignmentLog onChange={c => setPicker(c)} key={toast} /> : <main className="main">
          <TodayAdmissions q={q} onPicker={setPicker} onToast={setToast} reload={rev} />
        </main>}
      </div>
      {picker && <BedPicker caseId={picker} onClose={() => setPicker(null)} onDone={m => { setPicker(null); setToast(m); setRev(x => x + 1) }} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}
