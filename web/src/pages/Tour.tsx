import { useEffect, useState } from 'react'
import { api, ApiError, type TourStep } from '../api'
import { Logo } from '../icons'

// 시연 화면(P5): 대표 사례 3개를 단계별로. 로그인 없이 보고, 운영 화면 데이터와 섞이지 않는다.
export function Tour({ onHome }: { onHome: () => void }) {
  const [chapters, setChapters] = useState<{ id: string; title: string; summary: string; steps: number }[]>([])
  const [cur, setCur] = useState<TourStep | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { api.tourChapters().then(r => { setChapters(r.chapters); if (r.chapters[0]) open(r.chapters[0].id) }) }, [])
  const open = async (id: string) => {
    setBusy(true); setErr('')
    try { setCur(await api.tourStart(id)) } catch (e) { setErr(e instanceof ApiError ? e.message : '시연을 시작하지 못했습니다') } finally { setBusy(false) }
  }
  const next = async () => {
    if (!cur) return
    if (cur.done) {
      const i = chapters.findIndex(c => c.id === cur.chapter)
      if (chapters[i + 1]) open(chapters[i + 1].id)
      return
    }
    setBusy(true)
    try { setCur(await api.tourNext(cur.run)) } catch (e) { setErr(e instanceof ApiError ? e.message : '다음 단계로 가지 못했습니다') } finally { setBusy(false) }
  }
  const last = cur && cur.done && chapters.findIndex(c => c.id === cur.chapter) === chapters.length - 1
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <span className="mode-tag">시연</span>
        <div className="ab-r"><button className="btn sm" onClick={onHome}>처음 화면</button></div>
      </header>
      <div className="tour">
        <aside className="tour-list" aria-label="사례">
          {chapters.map((c, i) => (
            <button key={c.id} className={cur?.chapter === c.id ? 'on' : ''} onClick={() => open(c.id)} disabled={busy}>
              <span className="num">{i + 1}</span><span><b>{c.title}</b><span>{c.steps}단계</span></span></button>
          ))}
          <p className="note">합성 데이터로 만든 병원에서 실제와 같은 코드가 돌아갑니다. 운영 화면과는 따로 움직입니다.</p>
        </aside>
        <main className="tour-main">
          {!cur ? <div className="empty">{err || '시연을 준비하는 중'}</div> : (<>
            <div className="tour-head">
              <div><div className="lab">{cur.chapter_title}</div><h1>{cur.title}</h1></div>
              <div className="tour-meta">{cur.now && <span className="tag t-gray">병원 시각 {cur.now}</span>}<span className="tag t-blue">{cur.step} / {cur.steps}단계</span></div>
            </div>
            <div className="progress"><i style={{ width: `${(cur.step / cur.steps) * 100}%` }} /></div>
            <p className="tour-sum">{cur.summary}</p>
            <section className="card"><h3>지금 일어난 일</h3><p className="tour-cap">{cur.caption}</p></section>
            {cur.look.length > 0 && <section className="card"><h3>볼 것</h3><ul className="look">{cur.look.map(l => <li key={l}>{l}</li>)}</ul></section>}
            {cur.changed.length > 0 && (
              <section className="card"><h3>이번 단계에서 남은 기록</h3>
                <table><thead><tr><th>시각</th><th>누가</th><th>환자</th><th>무엇을</th><th>내용</th></tr></thead>
                  <tbody>{cur.changed.map((c, i) => <tr key={i}><td className="mono">{c.time}</td><td>{c.actor}</td><td>{c.patient}</td><td>{c.what}</td><td className="sub">{c.detail}</td></tr>)}</tbody></table>
              </section>)}
            {err && <div className="err">{err}</div>}
            <div className="tour-actions">
              {last ? <button className="btn p" onClick={onHome}>처음 화면으로</button>
                : <button className="btn p" disabled={busy} onClick={next}>{busy ? '준비 중' : cur.done ? '다음 사례' : '다음 단계'}</button>}
            </div>
          </>)}
        </main>
      </div>
    </div>
  )
}
