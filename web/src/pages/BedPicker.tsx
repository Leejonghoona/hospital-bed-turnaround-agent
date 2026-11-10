import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type BoardBed } from '../api'

const FIT_LABEL: Record<string, string> = { ok: '배정 가능', reason: '차선책 가능', ipc: '감염관리실 승인 필요', no: '배정 불가' }

// 병상 직접 지정: 왼쪽은 환자 조건, 추천 병상, 사유와 확정. 오른쪽은 병동→병실→병상 현황판.
// 병상마다 고를 수 있는지는 서버(게이트웨이 사전 검사)가 정하고, 확정도 게이트웨이가 다시 검사한다.
export function BedPicker({ caseId, onClose, onDone }: { caseId: string; onClose: () => void; onDone: (msg: string) => void }) {
  const [b, setB] = useState<Board | null>(null)
  const [pick, setPick] = useState<(BoardBed & { where: string }) | null>(null)
  const [reason, setReason] = useState('')
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<string | null>(null)
  const bRef = useRef<Board | null>(null)
  bRef.current = b
  useEffect(() => { api.beds(caseId).then(x => { setB(x); setTab(x.wards[0]?.id ?? null) }).catch(() => setErr('병상 현황을 불러오지 못했습니다')) }, [caseId])
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && !(document.activeElement instanceof HTMLTextAreaElement || document.activeElement instanceof HTMLSelectElement)) {
        setTab(t => { const ws = bRef.current?.wards ?? []; const i = ws.findIndex(w => w.id === t); const n = ws[i + (e.key === 'ArrowRight' ? 1 : -1)]; return n ? n.id : t })
      }
    }
    window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k)
  }, [onClose])

  const all = b ? b.wards.flatMap(w => w.rooms.flatMap(r => r.beds.map(x => ({ ...x, where: `${w.name} ${r.label} ${x.label}` })))) : []
  const choose = (id: string) => { const x = all.find(y => y.id === id); if (x && x.fit !== 'no') { setPick(x); setErr('') } }
  const needMemo = reason === 'other' || pick?.fit === 'reason'
  const submit = async () => {
    if (!pick) return
    setBusy(true); setErr('')
    try {
      await api.assign(caseId, pick.id, reason, memo)
      onDone(`${b?.patient}: ${all.find(x => x.id === pick.id)?.where}로 배정했습니다. 병동에 알렸습니다`)
    } catch (e) { setErr(e instanceof ApiError ? e.message : '배정하지 못했습니다') } finally { setBusy(false) }
  }

  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-label="병상 직접 지정" onClick={onClose}>
      <div className="picker" onClick={e => e.stopPropagation()}>
        <header className="pk-h">
          <h2>병상 직접 지정{b && <span>{b.patient}</span>}</h2>
          <button className="btn sm" onClick={onClose}>닫기</button>
        </header>
        {!b ? <div className="empty">{err || '불러오는 중'}</div> : (
          <div className="pk-body">
            <aside className="pk-side">
              <div className="lab">환자 조건</div>
              <dl className="facts">
                <dt>성별</dt><dd>{b.need.sex}</dd>
                <dt>격리</dt><dd>{b.need.isolation}</dd>
                <dt>진료과</dt><dd>{b.need.department}</dd>
                <dt>구분</dt><dd>{b.need.specialty || '—'}</dd>
                <dt>희망 병실</dt><dd>{b.need.grade_wish}</dd>
                <dt>지금 병상</dt><dd>{b.current || '배정 전'}</dd>
              </dl>
              <div className="lab">추천 병상</div>
              {b.recommend.length === 0 ? <div className="note">지금 조건에 맞는 병상이 없습니다. 오른쪽 현황판에서 오늘 비는 병상을 확인하세요.</div> : (
                <div className="recs">{b.recommend.map((r, i) => (
                  <button key={r.bed} className={pick?.id === r.bed ? 'on' : ''} onClick={() => choose(r.bed)}>
                    <b>{i + 1}순위 {r.label}</b><span>{r.ready}</span><span>{r.basis}</span></button>
                ))}</div>
              )}
              {pick && (<>
                <div className="lab">선택한 병상</div>
                <div className="picked"><b>{pick.where}</b><span className={'fit ' + pick.fit}>{FIT_LABEL[pick.fit]}</span>{pick.why && <span className="why">{pick.why}</span>}</div>
                <div className="field"><label htmlFor="rs">배정 사유</label>
                  <select id="rs" value={reason} onChange={e => setReason(e.target.value)}>
                    <option value="">사유를 고르세요</option>
                    {b.reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}
                  </select></div>
                <div className="field"><label htmlFor="pm">메모 {needMemo ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
                  <textarea id="pm" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="병동과 다음 근무자가 알아야 할 내용" /></div>
                {err && <div className="err">{err}</div>}
                <button className="btn p full" disabled={busy || !reason || (needMemo && !memo.trim())} onClick={submit}>이 병상으로 배정</button>
              </>)}
            </aside>
            <section className="pk-board">
              <div className="legend" title="← → 키로 병동을 넘길 수 있습니다">
                <span><i className="ok" />배정 가능</span><span><i className="reason" />차선책 가능</span>
                <span><i className="ipc" />감염관리실 승인 필요</span><span><i className="no" />배정 불가</span>
              </div>
              <div className="wtabs" role="tablist" aria-label="병동">
                {b.wards.map(w => {
                  const free = w.rooms.flatMap(r => r.beds).filter(x => x.fit !== 'no').length
                  return (
                    <button key={w.id} role="tab" aria-selected={tab === w.id} className={tab === w.id ? 'on' : ''} onClick={() => setTab(w.id)}>
                      <b>{w.name}</b><span>{w.specialty}</span>
                      <em className={free ? 'has' : ''}>{free ? `가능 ${free}` : '가능 없음'}</em></button>)
                })}
              </div>
              {b.wards.filter(w => w.id === tab).map(w => (
                <div key={w.id} className="wd">
                  <div className="rooms">{w.rooms.map(r => (
                    <div key={r.id} className="room">
                      <div className="rh"><b>{r.label}</b><span>{r.grade}{r.sex && ` ${r.sex}`}{r.cohort && ` 코호트`}</span></div>
                      <div className="bs">{r.beds.map(x => (
                        <button key={x.id} className={'bd ' + x.fit + (pick?.id === x.id ? ' on' : '')} disabled={x.fit === 'no'}
                          title={x.why || FIT_LABEL[x.fit]} onClick={() => choose(x.id)} aria-label={`${w.name} ${r.label} ${x.label} ${x.status}`}>
                          <b>{x.label}</b><span>{x.status}</span></button>
                      ))}</div>
                    </div>
                  ))}</div>
                </div>
              ))}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
