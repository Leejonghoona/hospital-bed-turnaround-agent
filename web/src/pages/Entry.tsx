import { Icon, Logo } from '../icons'

// 첫 화면(세로 구조): 머리말 → 한 줄 소개 → 고르기(같은 크기 카드) → 이렇게 일합니다(3단계).
// 숫자 대신 처음 보는 사람이 바로 이해하는 문장으로 쓴다(사용자 지적 2026-11-10).
const STEPS = [
  { n: '1', t: '퇴원이 정해지면 추적을 시작합니다', d: '처방, 수납, 퇴실, 청소까지 병상이 비는 과정을 따라갑니다' },
  { n: '2', t: '평범한 배정은 에이전트가 처리합니다', d: '조건에 맞는 병상을 확정하고 원무와 병동에 알립니다' },
  { n: '3', t: '판단이 필요한 일만 담당자에게 갑니다', d: '격리 결정이나 환자 연락처럼 사람이 책임질 일입니다' },
]

export function Entry({ onDemo, onOps, onLogin }: { onDemo: () => void; onOps: () => void; onLogin: () => void }) {
  return (
    <div className="entry2">
      <header className="e-top">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <button className="btn sm" onClick={onLogin}>로그인</button>
      </header>
      <section className="e-hero">
        <h1>퇴원부터 다음 입실까지<br />병상 흐름을 한 화면에서</h1>
        <p>평범한 배정은 에이전트가 처리하고, 담당자는 결정이 필요한 일만 봅니다</p>
      </section>
      <section className="e-choose">
        <div className="tile pri">
          <div className="ic"><Icon name="play" /></div>
          <h3>시연 화면</h3>
          <div className="d">대표 사례 3개를 단계별로 따라갑니다</div>
          <div className="go"><button className="btn p full" onClick={onDemo}>시연 시작</button></div>
        </div>
        <div className="tile">
          <div className="ic"><Icon name="panel" /></div>
          <h3>운영 화면</h3>
          <div className="d">역할 계정으로 하루 업무를 직접 처리합니다</div>
          <div className="go"><button className="btn full" onClick={onOps}>데모 계정으로 시작</button></div>
        </div>
      </section>
      <section className="e-how">
        <h2>이렇게 일합니다</h2>
        <ol>{STEPS.map(s => (
          <li key={s.n}><span className="num">{s.n}</span><div><b>{s.t}</b><span>{s.d}</span></div></li>
        ))}</ol>
      </section>
    </div>
  )
}
