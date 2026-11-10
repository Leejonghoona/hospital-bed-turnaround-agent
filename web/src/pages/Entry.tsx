import { Icon, Logo } from '../icons'

// 첫 화면(세로 구조): 머리말 → 한 줄 소개 → 고르기(같은 크기 카드) → 이렇게 일합니다(3단계).
// 숫자 대신 처음 보는 사람이 바로 이해하는 문장으로 쓴다(사용자 지적 2026-11-10). 단계 제목은 한 줄에 들어가게 짧게.
const STEPS = [
  { n: '1', t: '퇴원 결정부터 추적', d: ['처방, 수납, 퇴실, 청소까지', '병상이 비는 과정을 봅니다'] },
  { n: '2', t: '조건 맞는 배정은 자동 확정', d: ['원무와 병동에 알리고', '정해진 시간 안에 되돌릴 수 있습니다'] },
  { n: '3', t: '판단할 일만 담당자에게', d: ['격리 결정, 환자 연락처럼', '사람이 책임질 일입니다'] },
]

export function Entry({ onDemo, onOps }: { onDemo: () => void; onOps: () => void }) {
  return (
    <div className="entry2">
      <header className="e-top">
        <div className="brand"><Logo /><b>WardFlow</b></div>
      </header>
      <section className="e-hero">
        <h1>퇴원부터 다음 입실까지<br />병상 흐름을 한 화면에서</h1>
        <p>조건이 맞는 배정은 자동으로 확정하고, 담당자는 판단이 필요한 일만 봅니다</p>
      </section>
      <section className="e-choose">
        <div className="tile">
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
          <li key={s.n}><span className="num">{s.n}</span><div><b>{s.t}</b><span>{s.d.map(l => <span key={l} className="ln">{l}</span>)}</span></div></li>
        ))}</ol>
      </section>
    </div>
  )
}
