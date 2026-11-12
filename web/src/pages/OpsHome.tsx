import { Fragment, useEffect, useState } from 'react'
import { api, type OpsSummary, type User } from '../api'
import { Shell } from '../parts/Shell'
import { Stats } from '../parts/Stats'

// 원무팀장(병상 운영 책임자) 화면: 오늘 흐름. 퇴실, 병상 준비, 입실 대기, 배정 못 한 이유를 집계로 본다(환자 상세 없음, DECISIONS 2026-11-10).
export function OpsHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [sum, setSum] = useState<OpsSummary | null>(null)
  const [now, setNow] = useState<string | undefined>()
  useEffect(() => { api.opsSummary().then(setSum); api.me().then(m => setNow(m.now)).catch(() => {}) }, [])
  const un = sum ? Object.entries(sum.unassigned) : []
  return (
    <Shell user={user} nav={[['flow', '오늘 흐름']]} page="flow" onPage={() => {}} now={now} onLogout={onLogout} onSwitch={onSwitch}>
      <main className="main">
        <div className="ph"><h1>오늘 흐름</h1></div>
        {!sum ? <div className="empty">불러오는 중</div> : (<>
          <Stats items={[
            { label: '오늘 입원', value: sum.load['입원 사례'] },
            { label: '병상 확정', value: sum.load['확정'], sub: `에이전트 자동 확정 ${sum.load['사람 손을 안 거친 확정']}건` },
            { label: '수속부터 입실까지', value: sum.flow['수속부터 입실까지(평균)'] },
            { label: '아직 병상 없음', value: un.reduce((a, [, n]) => a + n, 0) },
          ]} />
          <div className="two-col">
            <section className="card"><h3>퇴실에서 입실까지</h3>
              <dl className="kv">{Object.entries(sum.flow).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
              <p className="note">{sum.note}</p></section>
            <section className="card"><h3>아직 병상이 없는 이유</h3>
              {un.length ? <dl className="kv">{un.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}명</dd></Fragment>)}</dl> : <p>오늘 입원은 모두 병상이 정해졌습니다</p>}
              <h3>안전 검사</h3>
              <table><thead><tr><th>지표</th><th>위반</th><th>검사 수</th></tr></thead>
                <tbody>{sum.safety.map(x => <tr key={x['지표']}><td>{x['지표']}</td><td><span className={'tag ' + (x['위반'] ? 't-red' : 't-green')}>{x['위반']}</span></td><td>{x['검사 수']}</td></tr>)}</tbody></table>
            </section>
          </div>
        </>)}
      </main>
    </Shell>
  )
}
