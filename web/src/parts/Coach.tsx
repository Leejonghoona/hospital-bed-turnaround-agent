import { useEffect, useState } from 'react'

// 시연(따라 해 보기): 실제 원무 화면 위에 작은 안내 카드. 한 단계에 한 가지만 짚고, 사용자가 직접 해 보면 다음으로 넘어간다.
// 근거: NN/g "Tutorials … are quickly forgotten", 실제 화면에서 과업을 직접 하게 하는 방식 권장(docs/화면재설계/10_UX_재설계.md)
type Step = { title: string; body: string; target?: () => Element | null; done?: () => boolean; button?: string }

const q = (sel: string) => document.querySelector(sel)
const toast = (re: RegExp) => re.test(q('.toast')?.textContent ?? '')
const byText = (sel: string, re: RegExp) => [...document.querySelectorAll(sel)].find(e => re.test(e.textContent ?? '')) ?? null
// 짚을 환자: 배지 없이 '안내 전'인 첫 환자 / '재연락' 배지가 있는 첫 환자 (처음 정한 행을 기억해 다른 행으로 바뀌지 않게)
let plainCase = '', recontactCase = ''
const rowOf = (c: string) => (c ? q(`tr.row[data-case="${c}"]`) : null)
const plainRow = () => {
  if (!rowOf(plainCase)) plainCase = ([...document.querySelectorAll('tr.row')].find(r => !r.querySelector('.badge') && /안내 전/.test(r.textContent ?? '') && /에이전트/.test(r.textContent ?? '')) as HTMLElement | undefined)?.dataset.case ?? ''
  return rowOf(plainCase)
}
const recontactRow = () => {
  if (!rowOf(recontactCase)) recontactCase = ([...document.querySelectorAll('tr.row')].find(r => /재연락/.test(r.querySelector('td.badges')?.textContent ?? '')) as HTMLElement | undefined)?.dataset.case ?? ''
  return rowOf(recontactCase)
}

const STEPS: Step[] = [
  { title: '오늘 입원 화면', body: '원무팀이 하루를 시작하는 화면입니다. 위의 숫자 막대에서 오늘 입원과, 그중 에이전트가 이미 병상을 확정한 수를 봅니다.', target: () => q('.stats'), button: '다음' },
  { title: '평범한 입원은 에이전트가 처리', body: '반짝이는 환자를 눌러 보세요. 배지가 없는 이 환자는 에이전트가 조건에 맞는 병상을 확정하고 병동에 알렸습니다.', target: plainRow,
    done: () => !!plainRow()?.classList.contains('pick') },
  { title: '사람은 환자 연락만', body: '환자에게 수속 시각을 안내했다고 기록해 보세요. 반짝이는 "연락함으로 기록"을 누릅니다.', target: () => byText('aside.detail button', /연락함으로 기록/),
    done: () => toast(/입원 안내를 기록/) },
  { title: '판단이 필요한 일은 배지로', body: '배지가 붙은 환자는 사람이 판단할 일입니다. 반짝이는 "재연락" 환자를 눌러 보세요.', target: recontactRow,
    done: () => !!recontactRow()?.classList.contains('pick') },
  { title: '승인해도 시스템이 다시 확인', body: '안내한 시각을 지키기 어려워 에이전트가 재연락을 제안했습니다. 반짝이는 "승인"을 누르면, 실행 직전에 시스템이 조건을 다시 확인합니다.',
    target: () => { const box = byText('aside.detail .issue', /재연락/); return box ? byText('aside.detail .issue button', /^승인$/) : null },
    done: () => toast(/다시 확인/) },
  { title: '확정 근거는 배정 기록에', body: '반짝이는 "배정 기록"을 누르면 오늘 에이전트와 원무팀이 확정한 배정과 그 이유, 되돌리기 시한이 보입니다.', target: () => byText('.gnav button', /배정 기록/),
    done: () => /배정 기록/.test(q('.ph h1')?.textContent ?? '') },
  { title: '끝', body: '평범한 배정은 에이전트가, 판단이 필요한 일만 사람이. 병동과 감염관리실 화면도 같은 원칙입니다. 이 시연에서 한 일은 운영 데이터에 남지 않습니다.' },
]

export function Coach({ onExit }: { onExit: () => void }) {
  const [i, setI] = useState(0)
  const [min, setMin] = useState(false)
  const s = STEPS[i]
  useEffect(() => {
    const mark = () => {
      const el = s.target ? s.target() : null
      document.querySelectorAll('.coach-pulse').forEach(e => { if (e !== el) e.classList.remove('coach-pulse') })
      el?.classList.add('coach-pulse')
      if (s.done && s.done()) setI(x => Math.min(x + 1, STEPS.length - 1))
    }
    mark()
    const t = setInterval(mark, 400)
    return () => { clearInterval(t); document.querySelectorAll('.coach-pulse').forEach(e => e.classList.remove('coach-pulse')) }
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
