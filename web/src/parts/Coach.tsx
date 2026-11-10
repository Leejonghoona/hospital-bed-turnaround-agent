import { useEffect, useRef, useState } from 'react'
import { api, type Diff, type User } from '../api'

// 시연: 1부는 원무팀이 하루에 하는 일을 실제 화면에서 따라 하고("시연 1/7"), 2부는 구현된 화면을 차례로 띄워 차별점을 하나씩 짚는다("차별점 1/6").
// 짚는 곳은 테두리가 반짝인다. 다른 팀 화면이 필요하면 시연 세션이 그 팀 계정으로 바뀐다(같은 시연용 합성 병원, 운영 데이터와 따로).
// 근거: NN/g "Tutorials … are quickly forgotten", 실제 화면에서 과업을 직접 하게 하는 방식 권장(docs/화면재설계/10_UX_재설계.md)
type Step = { part: 'flow' | 'diff' | 'summary'; title: string; body: string; as: string; key?: string; tab?: string
  target?: () => Element | null; done?: () => boolean; button?: string }

const q = (sel: string) => document.querySelector(sel)
const toast = (re: RegExp) => re.test(q('.toast')?.textContent ?? '')
const byText = (sel: string, re: RegExp) => [...document.querySelectorAll(sel)].find(e => re.test(e.textContent ?? '')) ?? null
// 짚을 환자: 배지 없이 '안내 전'이고 에이전트가 확정한 첫 환자 / '재연락' 배지가 있는 첫 환자 (처음 정한 행을 기억해 다른 행으로 바뀌지 않게)
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

const ADM = 'adm-kim', IPC = 'ipc-lee', WARD = 'ward71-park', MI = 'admin-jung'
const STEPS: Step[] = [
  { part: 'flow', as: ADM, title: '오늘 입원 화면', body: '원무팀이 하루를 시작하는 화면입니다. 위의 숫자 막대에서 오늘 입원과, 그중 에이전트가 이미 병상을 확정한 수를 봅니다.', target: () => q('.stats'), button: '다음' },
  { part: 'flow', as: ADM, title: '평범한 입원은 에이전트가 처리', body: '반짝이는 환자를 눌러 보세요. 배지가 없는 이 환자는 에이전트가 조건에 맞는 병상을 확정하고 병동에 알렸습니다.', target: plainRow,
    done: () => !!plainRow()?.classList.contains('pick') },
  { part: 'flow', as: ADM, title: '사람은 환자 연락만', body: '환자에게 수속 시각을 안내했다고 기록해 보세요. 반짝이는 "연락함으로 기록"을 누릅니다.', target: () => byText('aside.detail button', /연락함으로 기록/),
    done: () => toast(/입원 안내를 기록/) },
  { part: 'flow', as: ADM, title: '판단이 필요한 일은 배지로', body: '배지가 붙은 환자는 사람이 판단할 일입니다. 반짝이는 "재연락" 환자를 눌러 보세요.', target: recontactRow,
    done: () => !!recontactRow()?.classList.contains('pick') },
  { part: 'flow', as: ADM, title: '승인하면 시스템이 실행', body: '안내한 시각을 지키기 어려워 에이전트가 재연락을 제안했습니다. 반짝이는 "승인"을 눌러 보세요.',
    target: () => { const box = byText('aside.detail .issue', /재연락/); return box ? byText('aside.detail .issue button', /^승인$/) : null },
    done: () => toast(/다시 확인/) },
  { part: 'flow', as: ADM, title: '확정 근거는 배정 기록에', body: '반짝이는 "배정 기록"을 누르면 오늘 에이전트와 원무팀이 확정한 배정과 그 이유, 되돌리기 시한이 보입니다.', target: () => byText('.gnav button', /배정 기록/),
    done: () => /배정 기록/.test(q('.ph h1')?.textContent ?? '') },
  { part: 'flow', as: ADM, title: '원무팀의 하루', body: '평범한 배정은 에이전트가 하고, 사람은 환자 연락과 판단이 필요한 일만 합니다. 이어서 다른 시스템과 다른 점을 각 팀 화면에서 짚어 봅니다.', button: '차별점 보기' },

  { part: 'diff', key: 'G2', as: IPC, title: '늦게 온 격리 정보 반영', body: '감염관리실 화면입니다. 병상이 정해진 뒤 격리 오더가 나면, 시스템이 입실 호출을 멈추고 반짝이는 환자처럼 감염관리실 결정으로 올립니다.',
    target: () => byText('tr.row', /병상 결정, 마감/), button: '다음' },
  { part: 'diff', key: 'A', as: WARD, title: '병상 먼저 만들기', body: '병동 화면입니다. 환자가 퇴실하면 시스템이 곧바로 청소를 요청합니다. 반짝이는 줄에서 퇴실 시각과 청소 요청 시각을 비교해 보세요.',
    target: () => q('.clean-req')?.closest('tr') ?? null, button: '다음' },
  { part: 'diff', key: 'G1', as: MI, tab: '개요', title: '실행 직전 재검사', body: '의료정보팀의 AI 운영 화면입니다. 실행 직전 검사에 걸린 판단은 실행되지 않고, 이유와 함께 여기에 남습니다.',
    target: () => q('.card.blocked'), button: '다음' },
  { part: 'diff', key: 'C', as: MI, tab: '자율 범위', title: '기록이 정하는 자율 범위', body: '판단 유형마다 AI가 확정할지, 사람이 확인할지 정합니다. 사람 결정과 충분히 일치한 유형만 AI에 맡길 수 있고, 격리는 늘 사람이 확인합니다.',
    target: () => q('.card.settings'), button: '다음' },
  { part: 'diff', key: 'B', as: MI, tab: '병원 연동', title: '온톨로지로 병원 데이터 연결', body: '병원 EMR의 표와 코드를 공통 개념 체계(온톨로지)에 연결합니다. AI가 확신하지 못한 것만 묻고, 하루 시험 운영을 통과해야 승인할 수 있습니다. 환자 데이터를 따로 올리는 기능이 아닙니다.',
    target: () => q('.card.onboard'), button: '다음' },
  { part: 'diff', key: 'LLM', as: MI, tab: '모델과 평가', title: '시험을 통과한 AI만 사용', body: '배정과 숫자 계산은 AI가 아니라 규칙 코드가 합니다. AI는 시험을 통과한 일에만 쓰고, 바꿀 때마다 같은 시험을 다시 봅니다.',
    target: () => q('.grid2 > .card'), button: '정리 보기' },
  { part: 'summary', as: MI, title: '한눈에 정리', body: '' },
]
const FLOW_N = STEPS.filter(s => s.part === 'flow').length
const DIFF_N = STEPS.filter(s => s.part === 'diff').length

export function Coach({ user, onUser, onExit }: { user: User; onUser: (u: User) => void; onExit: () => void }) {
  const [i, setI] = useState(0)
  const [min, setMin] = useState(false)
  const [diff, setDiff] = useState<{ rows: Diff[]; note: string } | null>(null)
  const scrolled = useRef(-1)
  const s = STEPS[i]
  const n = STEPS.slice(0, i + 1).filter(x => x.part === s.part).length
  const metric = s.key ? diff?.rows.find(r => r.key === s.key) : undefined
  useEffect(() => { api.differentiators().then(setDiff).catch(() => {}) }, [])
  useEffect(() => { document.documentElement.classList.add('touring'); return () => { document.documentElement.classList.remove('touring'); document.querySelectorAll('.coach-room').forEach(e => e.classList.remove('coach-room')) } }, [])
  useEffect(() => {  // 이 단계가 보여 줄 팀 화면으로
    if (s.as !== user.user_id) api.tourAs(s.as).then(r => onUser(r.user)).catch(() => {})
  }, [s.as, user.user_id, onUser])
  useEffect(() => {
    const mark = () => {
      if (s.tab) { const b = byText('.subtabs button', new RegExp(`^${s.tab}$`)) as HTMLButtonElement | null; if (b && b.getAttribute('aria-selected') !== 'true') b.click() }
      const el = s.target ? s.target() : null
      document.querySelectorAll('.coach-pulse').forEach(e => { if (e !== el) e.classList.remove('coach-pulse') })
      if (el) {
        el.classList.add('coach-pulse')
        // 안내 카드는 늘 오른쪽 아래에 둔다(움직이면 거슬린다). 대신 짚는 곳을 카드 위로 스크롤한다(scroll-margin, styles.css)
        if (scrolled.current !== i) {
          scrolled.current = i
          // 짚는 곳을 담은 스크롤 영역 아래에 여백을 둬서, 맨 아래 줄도 안내 카드 위로 올라오게 한다
          for (let p: HTMLElement | null = el.parentElement; p; p = p.parentElement) {
            const o = getComputedStyle(p).overflowY
            if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight) { p.classList.add('coach-room'); break }
          }
          el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
        }
      }
      if (s.done && s.done()) setI(x => Math.min(x + 1, STEPS.length - 1))
    }
    mark()
    const t = setInterval(mark, 400)
    return () => { clearInterval(t); document.querySelectorAll('.coach-pulse').forEach(e => e.classList.remove('coach-pulse')) }
  }, [i, s])
  const label = s.part === 'flow' ? `시연 ${n}/${FLOW_N}` : `차별점 ${n}/${DIFF_N}`
  if (s.part === 'summary') return (
    <div className="scrim coach-ov" role="dialog" aria-modal="true" aria-label="차별점 한눈에 보기">
      <div className="ov">
        <div className="coach-h"><span className="mode-tag">시연</span><span className="sub">정리</span><button className="link" onClick={onExit}>끝내기</button></div>
        <h2>차별점 한눈에 보기</h2>
        {!diff ? <div className="empty">불러오는 중</div> : (
          <table className="diff">
            <colgroup><col style={{ width: 36 }} /><col style={{ width: 230 }} /><col /><col style={{ width: 190 }} /><col style={{ width: 170 }} /></colgroup>
            <thead><tr><th></th><th>차별점</th><th>무엇이 다른가</th><th>수치</th><th>화면</th></tr></thead>
            <tbody>{diff.rows.map((r, k) => (
              <tr key={r.key}><td className="no">{k + 1}</td><td><b>{r.title}</b></td><td className="sub">{r.what}</td>
                <td><b className="v">{r.value}</b><em>{r.value_sub}</em></td><td><span className="tag t-gray">{r.where}</span></td></tr>))}</tbody>
          </table>)}
        {diff && <p className="note">수치는 {diff.note}입니다.</p>}
        <div className="coach-a"><button className="btn" onClick={() => setI(i - 1)}>이전</button><button className="btn" onClick={() => setI(0)}>처음부터</button><button className="btn p" onClick={onExit}>처음 화면</button></div>
      </div>
    </div>
  )
  if (min) return <button className="coach-min" onClick={() => setMin(false)}>{label}</button>
  return (
    <div className="coach" role="dialog" aria-label="시연 안내">
      <div className="coach-h"><span className="mode-tag">시연</span><b className={'dnum' + (s.part === 'diff' ? ' d' : '')}>{label}</b>
        <button className="link" onClick={() => setMin(true)}>접기</button><button className="link" onClick={onExit}>끝내기</button></div>
      <h3>{s.title}</h3>
      <p>{s.body}</p>
      {metric && <div className="coach-m"><b>{metric.value}</b><span>{metric.value_sub}</span><em>{diff?.note}</em></div>}
      <div className="coach-a">
        {i > 0 && <button className="btn sm" onClick={() => setI(i - 1)}>이전</button>}
        {s.button && <button className="btn sm p" onClick={() => setI(i + 1)}>{s.button}</button>}
        {s.done && <button className="btn sm" onClick={() => setI(i + 1)}>건너뛰기</button>}
      </div>
    </div>
  )
}
