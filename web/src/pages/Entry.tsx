import { Icon, Logo } from '../icons'

// 첫 화면: 시연 화면 / 운영 화면 (같은 크기 카드). 시안 v2 ①.
export function Entry({ onDemo, onOps, onLogin }: { onDemo: () => void; onOps: () => void; onLogin: () => void }) {
  return (
    <div className="entry">
      <div className="l">
        <div className="deco" />
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <div className="h1">퇴원부터 다음 입실까지<br />병상 흐름을 한 화면에서</div>
        <div className="lead">평범한 배정은 에이전트가 처리하고<br />담당자는 결정이 필요한 일만 봅니다</div>
        <div className="stat">
          <div><b>72%</b><span>사람 손 없이 확정</span></div>
          <div><b>−13%</b><span>수속에서 입실까지</span></div>
          <div><b>0건</b><span>안전 규칙 위반</span></div>
        </div>
      </div>
      <div className="r">
        <h2>시작하기</h2>
        <p>보는 방법을 고르세요</p>
        <div className="tiles">
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
        </div>
        <div className="signin">계정이 있으신가요?<button onClick={onLogin}>로그인</button></div>
      </div>
    </div>
  )
}
