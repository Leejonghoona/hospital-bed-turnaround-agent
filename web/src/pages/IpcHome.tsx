import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type IpcHome as IH, type User } from '../api'
import { Shell, useToast } from '../parts/Shell'

// 감염관리실 화면(P4): 늦은 격리의 병상 결정(감염관리실만 결정), 종말소독 대기 병실, 오늘 격리 오더.
export function IpcHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [page, setPage] = useState('decide')
  const [d, setD] = useState<IH | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [now, setNow] = useState<string | undefined>()
  const [toast, setToast] = useToast()
  const load = useCallback(() => api.ipc().then(x => { setD(x); setPick(p => p && x.approvals.some(a => a.exception_id === p) ? p : x.approvals[0]?.exception_id ?? null) }), [])
  useEffect(() => { load(); api.me().then(m => setNow(m.now)).catch(() => {}) }, [load])
  const cur = d?.approvals.find(a => a.exception_id === pick) ?? null
  return (
    <Shell user={user} nav={[['decide', '격리 결정'], ['rooms', '병실·오더']]} page={page} onPage={setPage} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>{page === 'decide' ? '격리 결정' : '병실·오더'}</h1><span className="meta">지금 결정 담당: {d?.holder}</span></div>
        {page === 'decide' ? (
          <div className="work">
            <section className="grid"><div className="tbody">
              {d && d.approvals.length === 0 ? <div className="empty">결정할 늦은 격리가 없습니다</div> : (
                <table>
                  <colgroup><col style={{ width: 90 }} /><col style={{ width: 160 }} /><col style={{ width: 150 }} /><col /></colgroup>
                  <thead><tr><th>마감</th><th>환자</th><th>지금 병상</th><th>상황</th></tr></thead>
                  <tbody>{d?.approvals.map(a => (
                    <tr key={a.exception_id} className={'row' + (a.exception_id === pick ? ' pick' : '')} onClick={() => setPick(a.exception_id)} tabIndex={0}>
                      <td className="due">{a.deadline}</td><td className="pt"><b>{a.patient}</b></td><td>{a.bed}</td>
                      <td className="sub">{a.trigger}, {a.organism} {a.precautions}</td>
                    </tr>))}</tbody>
                </table>)}
            </div></section>
            <aside className="detail" aria-label="상세">
              {!cur ? <div className="empty">왼쪽에서 고르세요</div> : <IpcDecide key={cur.exception_id} a={cur} reasons={d?.decline_reasons ?? []} onDone={m => { setToast(m); load() }} />}
            </aside>
          </div>
        ) : (
          <div className="two-col">
            <section className="card"><h3>종말소독 대기 병실</h3>
              {d?.terminal.length ? <table><thead><tr><th>병상</th><th>퇴실</th><th>상태</th></tr></thead>
                <tbody>{d.terminal.map(t => <tr key={t.bed}><td>{t.bed}</td><td>{t.departed}</td><td><span className="tag t-amber">{t.status}</span></td></tr>)}</tbody></table>
                : <div className="empty">없습니다</div>}
              <p className="note">종말소독이 끝나기 전에는 같은 병실 병상을 배정할 수 없습니다. 비어 있는 음압 1인실 {d?.np_free ?? 0}개.</p>
            </section>
            <section className="card"><h3>오늘 격리 오더</h3>
              {d?.orders.length ? <table><thead><tr><th>시각</th><th>환자</th><th>주의</th><th>병원체</th></tr></thead>
                <tbody>{d.orders.map((o, i) => <tr key={i}><td>{o.time}</td><td>{o.case}</td><td>{o.precautions}</td><td>{o.organism}</td></tr>)}</tbody></table>
                : <div className="empty">없습니다</div>}
            </section>
          </div>
        )}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </Shell>
  )
}

function IpcDecide({ a, reasons, onDone }: { a: IH['approvals'][number]; reasons: string[]; onDone: (m: string) => void }) {
  const [decl, setDecl] = useState(false)
  const [reason, setReason] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const run = async (verb: 'approve' | 'decline') => {
    setErr('')
    try {
      const r = await api.ipcDecide(a.exception_id, verb, reason ?? '', memo)
      onDone(verb === 'approve' ? (r.status === 'executed' ? '승인했습니다. 다른 병상을 다시 찾고 병동과 원무에 알립니다' : '승인했지만 다시 확인한 결과 실행하지 않았습니다') : '지금 병상을 유지하도록 결정했습니다')
    } catch (e) { setErr(e instanceof ApiError ? e.message : '처리하지 못했습니다') }
  }
  return (<>
    <div className="dh"><div className="top"><span className="nm">{a.patient}</span><span className="id">{a.who}</span></div>
      <dl className="facts two"><dt>지금 병상</dt><dd>{a.bed}</dd><dt>마감</dt><dd>{a.deadline}</dd>
        <dt>격리</dt><dd>{a.precautions}</dd><dt>병원체</dt><dd>{a.organism}</dd></dl></div>
    <div className="db">
      <div className="lab">{a.action}</div>
      <p className="sit">{a.situation}</p><p className="eff">{a.effect}</p>
      {a.recommend.length > 0 && <><div className="lab">지금 추천</div>{a.recommend.map(r => <p key={r} className="note">{r}</p>)}</>}
      {err && <div className="err">{err}</div>}
      {!decl ? (
        <div className="btns"><button className="btn" onClick={() => setDecl(true)}>지금 병상 유지 등</button><button className="btn p" onClick={() => run('approve')}>다른 병상 찾기 승인</button></div>
      ) : (<>
        <div className="lab">결정 사유</div>
        <div className="note">1인실이 없으면 다인실에서 커튼과 1m 간격으로 제자리 격리할 수 있습니다(질병관리청 2026 관리지침).</div>
        <div className="seg col">{reasons.map(r => <button key={r} className={reason === r ? 'on' : ''} onClick={() => setReason(r)}>{r}</button>)}</div>
        <div className="field"><label htmlFor="im">메모 {reason === '기타' ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
          <textarea id="im" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="병동이 지켜야 할 조치 등" /></div>
        <div className="btns"><button className="btn" onClick={() => setDecl(false)}>취소</button>
          <button className="btn p" disabled={!reason || (reason === '기타' && !memo.trim())} onClick={() => run('decline')}>결정</button></div>
      </>)}
    </div>
  </>)
}
