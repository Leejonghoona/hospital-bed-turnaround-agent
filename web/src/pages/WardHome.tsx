import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type User, type WardBoard, type WardDischarge, type WardIncoming, type WardRow } from '../api'
import { Shell, useToast } from '../parts/Shell'
import { Stats } from '../parts/Stats'

const STEP_TONE: Record<string, string> = { '준비 전': 't-amber', '준비 완료': 't-green', '호출됨': 't-blue' }


// 병동 화면(P3): 우리 병동으로 올 환자와 준비 상태, 병상 현황. 병동은 배정을 직접 풀지 않고 원무에 의견을 보낸다.
export function WardHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [inc, setInc] = useState<WardIncoming | null>(null)
  const [board, setBoard] = useState<WardBoard | null>(null)
  const [dis, setDis] = useState<WardDischarge[]>([])
  const [pick, setPick] = useState<string | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const load = useCallback(() => {
    api.wardIncoming().then(x => { setInc(x); setPick(p => p && x.rows.some(r => r.case === p) ? p : x.rows[0]?.case ?? null) })
    api.wardBoard().then(setBoard); api.wardDischarges().then(r => setDis(r.rows))
  }, [])
  useEffect(() => { load(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [load])
  const cur = inc?.rows.find(r => r.case === pick) ?? null
  const beds = board?.rooms.flatMap(r => r.beds) ?? []
  const n = (k: string[]) => beds.filter(b => k.includes(b.state)).length
  const incomingBeds = new Set(inc?.rows.map(r => r.bed) ?? [])
  return (
    <Shell user={user} nav={[['today', '우리 병동']]} page="today" onPage={() => {}} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{inc?.ward ?? '우리 병동'}</h1></div>
        <Stats items={[
          { label: '오늘 퇴원', value: dis.length, sub: `퇴실 ${dis.filter(d => d.departed).length}명` },
          { label: '오늘 입실 예정', value: inc?.rows.length ?? 0, sub: `준비 완료 ${inc?.rows.filter(r => r.step !== '준비 전').length ?? 0}명` },
          { label: '빈 병상, 입실 가능', value: n(['empty', 'ready']) },
          { label: '청소 대기, 청소 중', value: n(['dirty', 'cleaning']) },
        ]} />
        <div className="ward-grid">
          <section className="pk-board ward-board">
            <div className="legend"><span><i className="ok" />빈 병상, 입실 가능</span><span><i className="reason" />청소 대기, 청소 중</span><span><i className="ipc" />퇴원 진행</span><span><i className="no" />재원 중</span><span className="in-mark">▲ 오늘 입실 예정</span></div>
            <div className="rooms">{board?.rooms.map(r => (
              <div key={r.id} className="room">
                <div className="rh"><b>{r.label}</b><span>{r.grade}{r.sex && ` ${r.sex}`}</span></div>
                <div className="bs">{r.beds.map(b => {
                  const short = `${b.id.split('-')[0].slice(1)}-${b.id.split('-')[1].slice(1)}-${b.id.split('-')[2].slice(1)}`
                  return <div key={b.id} className={'bd st-' + b.state} title={b.detail}><b>{b.label}{incomingBeds.has(short) && <span className="in-mark"> ▲</span>}</b><span>{b.state_ko}</span></div>
                })}</div>
              </div>))}</div>
          </section>
          <div className="ward-side">
            <section className="card">
              <h3>오늘 입실 <span className="cnt">{inc?.rows.length ?? 0}</span></h3>
              {inc && inc.rows.length === 0 ? <p className="note">오늘 입실 예정 환자가 없습니다</p> : (
                <table className="sel"><thead><tr><th>입실</th><th>환자</th><th>병상</th><th>상태</th></tr></thead>
                  <tbody>{inc?.rows.map(r => (
                    <tr key={r.case} className={'row' + (r.case === pick ? ' pick' : '')} onClick={() => setPick(r.case)}>
                      <td className="due">{r.checkin}</td><td className="pt"><b>{r.patient}</b><span className="id">{r.age}세 {r.sex}</span></td>
                      <td className="mono">{r.bed}</td>
                      <td><span className={'tag ' + (STEP_TONE[r.step] ?? 't-gray')}>{r.step}</span>{r.hold && <span className="badge">호출 보류</span>}</td></tr>))}</tbody></table>)}
              {cur && <div className="ward-detail"><WardDetail key={cur.case} r={cur} reasons={inc?.reasons ?? []} onDone={m => { setToast(m); load() }} /></div>}
            </section>
            <section className="card">
              <h3>퇴원 진행 <span className="cnt">{dis.length}</span></h3>
              {dis.length === 0 ? <p className="note">오늘 퇴원이 없습니다</p> : (
                <table><thead><tr><th>병상</th><th>환자</th><th>단계</th><th></th></tr></thead>
                  <tbody>{dis.map(d => (
                    <tr key={d.case} className={d.departed ? 'quiet' : ''}><td className="mono">{d.bed}</td><td><b>{d.patient}</b> <span className="sub">{d.age}세 {d.sex}</span></td>
                      <td>{d.step}{d.departed && ` ${d.departed}`}</td><td>{d.clean_req && <span className="tag t-blue clean-req" title="퇴실을 감지해 시스템이 청소를 요청했습니다">청소 요청 {d.clean_req}</span>}{d.flags.map(f => <span key={f} className="badge">{f}</span>)}</td></tr>))}</tbody></table>)}
            </section>
          </div>
        </div>
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
      {r.hold && <><div className="lab">호출 보류</div><p className="sit">{r.hold}. 원무팀이 확인하고 있습니다.</p></>}
      {r.front_detail && <><div className="lab">앞 환자 진행</div><p className="sit">{r.front_detail}</p></>}
      {err && <div className="err">{err}</div>}
      {!op ? (
        <div className="btns">
          <button className="btn" onClick={() => setOp(true)}>원무팀에 의견 보내기</button>
          <button className="btn p" disabled={r.step !== '준비 전'} onClick={() => run(() => api.wardPrep(r.case), `${r.patient} 병상 준비 완료를 알렸습니다`)}>
            {r.step === '준비 전' ? '준비 완료' : '준비 완료됨'}</button>
        </div>
      ) : (<>
        <div className="lab">의견 사유</div>
        <div className="note">배정은 그대로 두고 원무팀에 알립니다. 원무팀이 유지하거나 병상을 바꿉니다.</div>
        <div className="seg col">{reasons.map(x => <button key={x} className={reason === x ? 'on' : ''} onClick={() => setReason(x)}>{x}</button>)}</div>
        <div className="field"><label htmlFor="wm">메모 {reason === '기타' ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
          <textarea id="wm" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="원무팀이 알아야 할 내용" /></div>
        <div className="btns">
          <button className="btn" onClick={() => setOp(false)}>취소</button>
          <button className="btn p" disabled={!reason || (reason === '기타' && !memo.trim())}
            onClick={() => run(() => api.wardOpinion(r.case, reason!, memo), '원무팀에 의견을 보냈습니다')}>의견 보내기</button>
        </div>
      </>)}
    </div>
  </>)
}
