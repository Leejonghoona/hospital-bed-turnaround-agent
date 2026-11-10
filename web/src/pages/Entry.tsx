import { Icon, Logo } from '../icons'

// 첫 화면(세로 구조): 머리말 → 한 줄 소개 → 고르기(같은 크기 카드). '이렇게 일합니다'는 사용자 결정으로 뺐다(2026-11-10).

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
          <div className="d">실제 원무 화면에서 직접 해 봅니다, 2분</div>
          <div className="go"><button className="btn p full" onClick={onDemo}>시연 시작</button></div>
        </div>
        <div className="tile">
          <div className="ic"><Icon name="panel" /></div>
          <h3>운영 화면</h3>
          <div className="d">역할 계정으로 하루 업무를 직접 처리합니다</div>
          <div className="go"><button className="btn full" onClick={onOps}>데모 계정으로 시작</button></div>
        </div>
      </section>
    </div>
  )
}
