import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type User, type WardBoard, type WardIncoming, type WardRow } from '../api'
import { Shell, useToast } from '../parts/Shell'

const STEP_TONE: Record<string, string> = { '준비 전': 't-amber', '준비 완료': 't-green', '호출됨': 't-blue' }
const FRONT_TONE: Record<string, string> = { '입실 가능': 't-green', '청소 중': 't-amber', '청소 대기': 't-amber', '퇴원 진행': 't-gray', '빈 병상': 't-green' }

// 병동 화면(P3): 우리 병동으로 올 환자와 준비 상태, 병상 현황. 병동은 배정을 직접 풀지 않고 원무에 의견을 보낸다.
export function WardHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('incoming')
  const [inc, setInc] = useState<WardIncoming | null>(null)
  const [board, setBoard] = useState<WardBoard | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const load = useCallback(() => api.wardIncoming().then(x => { setInc(x); setPick(p => p && x.rows.some(r => r.case === p) ? p : x.rows[0]?.case ?? null) }), [])
  useEffect(() => { load(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [load])
  useEffect(() => { if (page === 'board') api.wardBoard().then(setBoard) }, [page])
  const cur = inc?.rows.find(r => r.case === pick) ?? null
  return (
    <Shell user={user} nav={[['incoming', '입실 예정'], ['board', '병상 현황']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{page === 'incoming' ? '입실 예정' : '병상 현황'}</h1><span className="meta">{inc?.ward}</span></div>
        {page === 'incoming' ? (
          <div className="work">
            <section className="grid">
              <div className="tbody">
                {inc && inc.rows.length === 0 ? <div className="empty">입실 예정 환자가 없습니다</div> : (
                  <table>
                    <colgroup><col style={{ width: 96 }} /><col style={{ width: 150 }} /><col style={{ width: 84 }} /><col /><col style={{ width: 150 }} /><col style={{ width: 100 }} /></colgroup>
                    <thead><tr><th>입실</th><th>환자</th><th>병상</th><th>준비할 것</th><th>앞 환자</th><th>상태</th></tr></thead>
                    <tbody>{inc?.rows.map(r => (
                      <tr key={r.case} className={'row' + (r.case === pick ? ' pick' : '')} onClick={() => setPick(r.case)} tabIndex={0}>
                        <td className="due">{r.checkin}</td>
                        <td className="pt"><b>{r.patient}</b><span className="id">{r.age}세 {r.sex}</span></td>
                        <td className="mono">{r.bed}</td>
                        <td className="sub">{r.prep.join(', ')}</td>
                        <td><span className={'tag ' + (FRONT_TONE[r.front] ?? 't-gray')}>{r.front || '—'}</span></td>
                        <td className="st"><span className={'tag ' + (STEP_TONE[r.step] ?? 't-gray')}>{r.step}</span>{r.hold && <small>호출 보류</small>}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </div>
            </section>
            <aside className="detail" aria-label="상세">
              {!cur ? <div className="empty">왼쪽에서 환자를 고르세요</div> : <WardDetail key={cur.case} r={cur} reasons={inc?.reasons ?? []} onDone={m => { setToast(m); load() }} />}
            </aside>
          </div>
        ) : (
          <section className="pk-board ward-board">
            <div className="legend"><span><i className="ok" />입실 가능, 빈 병상</span><span><i className="reason" />청소 대기, 청소 중</span><span><i className="ipc" />퇴원 진행</span><span><i className="no" />재원 중</span></div>
            <div className="rooms">{board?.rooms.map(r => (
              <div key={r.id} className="room">
                <div className="rh"><b>{r.label}</b><span>{r.grade}{r.sex && ` ${r.sex}`}</span></div>
                <div className="bs">{r.beds.map(b => (
                  <div key={b.id} className={'bd st-' + b.state} title={b.detail}><b>{b.label}</b><span>{b.state_ko}</span></div>
                ))}</div>
              </div>
            ))}</div>
          </section>
        )}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </Shell>
  )
}

function WardDetail({ r, reasons, onDone }: { r: WardRow; reasons: string[]; onDone: (m: string) => void }) {
  const [op, setOp] = useState(false)
  const [reason, setReason] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const run = async (f: () => Promise<unknown>, m: string) => {
    setErr('')
    try { await f(); onDone(m) } catch (e) { setErr(e instanceof ApiError ? e.message : '처리하지 못했습니다') }
  }
  return (<>
    <div className="dh">
      <div className="top"><span className="nm">{r.patient}</span><span className="id">{r.age}세 {r.sex}</span></div>
      <dl className="facts two">
        <dt>병상</dt><dd>{r.bed}</dd><dt>입실</dt><dd>{r.checkin}</dd>
        <dt>앞 환자</dt><dd>{r.front || '—'}</dd><dt>배정</dt><dd>{r.by} {r.notified}</dd>
      </dl>
    </div>
    <div className="db">
      <div className="lab">준비할 것</div>
      <div className="chips">{r.prep.map(p => <span key={p} className="tag t-gray">{p}</span>)}</div>
      {r.hold && <><div className="lab">호출 보류</div><p className="sit">{r.hold}. 원무가 확인하고 있습니다.</p></>}
      {r.front_detail && <><div className="lab">앞 환자 진행</div><p className="sit">{r.front_detail}</p></>}
      {err && <div className="err">{err}</div>}
      {!op ? (
        <div className="btns">
          <button className="btn" onClick={() => setOp(true)}>원무에 의견 보내기</button>
          <button className="btn p" disabled={r.step !== '준비 전'} onClick={() => run(() => api.wardPrep(r.case), `${r.patient} 병상 준비 완료를 알렸습니다`)}>
            {r.step === '준비 전' ? '준비 완료' : '준비 완료됨'}</button>
        </div>
      ) : (<>
        <div className="lab">의견 사유</div>
        <div className="note">배정은 그대로 두고 원무에 알립니다. 원무가 유지하거나 병상을 바꿉니다.</div>
        <div className="seg col">{reasons.map(x => <button key={x} className={reason === x ? 'on' : ''} onClick={() => setReason(x)}>{x}</button>)}</div>
        <div className="field"><label htmlFor="wm">메모 {reason === '기타' ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
          <textarea id="wm" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="원무가 알아야 할 내용" /></div>
        <div className="btns">
          <button className="btn" onClick={() => setOp(false)}>취소</button>
          <button className="btn p" disabled={!reason || (reason === '기타' && !memo.trim())}
            onClick={() => run(() => api.wardOpinion(r.case, reason!, memo), '원무에 의견을 보냈습니다')}>의견 보내기</button>
        </div>
      </>)}
    </div>
  </>)
}
