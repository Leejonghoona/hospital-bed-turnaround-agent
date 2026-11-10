import { useEffect, useState } from 'react'

// 시연(따라 해 보기): 실제 원무 화면 위에 작은 안내 카드. 한 단계에 한 가지만 짚고, 사용자가 직접 해 보면 다음으로 넘어간다.
// 근거: NN/g "Tutorials … are quickly forgotten", 실제 화면에서 과업을 직접 하게 하는 방식 권장(docs/화면재설계/10_UX_재설계.md)
type Step = { title: string; body: string; target?: string; done?: () => boolean; button?: string }

const has = (sel: string) => !!document.querySelector(sel)
const toast = (re: RegExp) => re.test(document.querySelector('.toast')?.textContent ?? '')

const STEPS: Step[] = [
  { title: '오늘 입원 화면', body: '원무가 하루를 시작하는 화면입니다. 맨 위 한 줄에 오늘 입원과, 그중 에이전트가 이미 병상을 확정한 수가 보입니다.', target: '.sumline', button: '다음' },
  { title: '평범한 입원은 에이전트가 처리', body: '배지가 없는 줄은 에이전트가 조건에 맞는 병상을 확정하고 병동에 알린 환자입니다. 배지 없는 줄을 하나 눌러 보세요.', target: 'table',
    done: () => has('tr.row.pick') && !has('tr.row.pick .badge') },
  { title: '사람은 환자 연락만', body: '환자에게 수속 시각을 안내했다고 기록해 보세요. 오른쪽 아래 "연락함으로 기록"을 누릅니다.', target: '.issue.routine',
    done: () => toast(/입원 안내를 기록/) },
  { title: '판단이 필요한 일은 배지로', body: '배지가 붙은 줄은 사람이 판단할 일입니다. "재연락" 배지가 있는 줄을 눌러 보세요.', target: 'td.badges',
    done: () => /재연락/.test(document.querySelector('tr.row.pick td.badges')?.textContent ?? '') },
  { title: '승인해도 시스템이 다시 확인', body: '안내한 시각을 지키기 어려워 에이전트가 재연락을 제안했습니다. 승인을 눌러 보세요. 실행 직전에 시스템이 조건을 다시 확인합니다.', target: '.issue',
    done: () => toast(/다시 확인/) },
  { title: '확정 근거는 배정 기록에', body: '위쪽 "배정 기록"을 누르면 오늘 에이전트와 원무가 확정한 배정과 그 이유, 되돌리기 시한이 보입니다.', target: '.gnav',
    done: () => /배정 기록/.test(document.querySelector('.ph h1')?.textContent ?? '') },
  { title: '끝', body: '평범한 배정은 에이전트가, 판단이 필요한 일만 사람이. 병동과 감염관리실 화면도 같은 원칙입니다. 이 시연에서 한 일은 운영 데이터에 남지 않습니다.' },
]

export function Coach({ onExit }: { onExit: () => void }) {
  const [i, setI] = useState(0)
  const [min, setMin] = useState(false)
  const s = STEPS[i]
  useEffect(() => {
    const mark = () => {
      document.querySelectorAll('.coach-target').forEach(e => e.classList.remove('coach-target'))
      if (s.target) document.querySelector(s.target)?.classList.add('coach-target')
      if (s.done && s.done()) setI(x => Math.min(x + 1, STEPS.length - 1))
    }
    mark()
    const t = setInterval(mark, 400)
    return () => { clearInterval(t); document.querySelectorAll('.coach-target').forEach(e => e.classList.remove('coach-target')) }
  }, [i, s])
  if (min) return <button className="coach-min" onClick={() => setMin(false)}>시연 안내 {i + 1}/{STEPS.length}</button>
  return (
    <div className="coach" role="dialog" aria-label="시연 안내">
      <div className="coach-h"><span className="mode-tag">시연</span><span className="sub">{i + 1} / {STEPS.length}</span>
        <button className="link" onClick={() => setMin(true)}>접기</button><button className="link" onClick={onExit}>끝내기</button></div>
      <h3>{s.title}</h3>
      <p>{s.body}</p>
      <div className="coach-a">
        {i > 0 && <button className="btn sm" onClick={() => setI(i - 1)}>이전</button>}
        {s.button && <button className="btn sm p" onClick={() => setI(i + 1)}>{s.button}</button>}
        {s.done && <button className="btn sm" onClick={() => setI(i + 1)}>건너뛰기</button>}
        {i === STEPS.length - 1 && <><button className="btn sm" onClick={() => setI(0)}>처음부터</button><button className="btn sm p" onClick={onExit}>처음 화면</button></>}
      </div>
    </div>
  )
}
