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

  { part: 'diff', key: 'G2', as: IPC, title: '늦게 온 안전 정보도 되돌린다', body: '감염관리실 화면입니다. 병상이 확정된 뒤 격리 오더가 나면 시스템이 환자 호출을 멈추고, 반짝이는 환자처럼 "병상 결정"으로 감염관리실에 올립니다.',
    target: () => byText('tr.row', /병상 결정, 마감/), button: '다음' },
  { part: 'diff', key: 'A', as: WARD, title: '병상을 먼저 만든다', body: '병동 화면입니다. 환자가 퇴실하면 시스템이 바로 청소를 요청합니다. 반짝이는 줄의 "청소 요청" 시각이 퇴실 직후입니다.',
    target: () => q('.clean-req')?.closest('tr') ?? null, button: '다음' },
  { part: 'diff', key: 'G1', as: MI, tab: '개요', title: '실행 직전에 다시 검사', body: '의료정보팀 AI 운영 화면입니다. 에이전트 판단도, 사람이 승인한 일도 실행 직전에 게이트웨이가 병상과 안전 조건을 다시 검사합니다. 조건이 바뀌었으면 실행하지 않고 이유를 남깁니다.',
    target: () => q('.card.blocked'), button: '다음' },
  { part: 'diff', key: 'C', as: MI, tab: '자율 범위', title: '맡기는 범위는 기록이 정한다', body: '판단 유형마다 에이전트가 확정할지 사람이 확인할지 정합니다. 사람 결정과 충분히 일치한 유형만 위임을 권고하고, 격리 같은 유형은 사람 확인으로 고정됩니다.',
    target: () => q('.card.settings'), button: '다음' },
  { part: 'diff', key: 'B', as: MI, tab: '새 병원 설정', title: '새 병원은 데이터로 세팅', body: '병원 내보내기 표를 올리면 모르는 것만 묻고, 그 병원 데이터로 하루를 시험 운영한 뒤 의료정보팀과 감염관리 책임자가 승인합니다.',
    target: () => q('.aiops > .card'), button: '다음' },
  { part: 'diff', key: 'LLM', as: MI, tab: '모델과 평가', title: '모델은 바꿔 끼우고 시험으로 고른다', body: '배정과 숫자는 결정적 코드가 합니다. 모델은 설명과 추출만 맡고, 시험 기준을 넘지 못한 모델은 쓰지 않습니다.',
    target: () => q('.grid2 > .card'), button: '정리 보기' },
  { part: 'summary', as: MI, title: '한눈에 정리', body: '' },
]
const FLOW_N = STEPS.filter(s => s.part === 'flow').length
const DIFF_N = STEPS.filter(s => s.part === 'diff').length

export function Coach({ user, onUser, onExit }: { user: User; onUser: (u: User) => void; onExit: () => void }) {
  const [i, setI] = useState(0)
  const [min, setMin] = useState(false)
  const [left, setLeft] = useState(false)  // 안내 카드가 짚는 곳을 가리면 반대편으로
  const [diff, setDiff] = useState<{ rows: Diff[]; note: string } | null>(null)
  const scrolled = useRef(-1)
  const flipped = useRef(-1)
  const s = STEPS[i]
  const n = STEPS.slice(0, i + 1).filter(x => x.part === s.part).length
  const metric = s.key ? diff?.rows.find(r => r.key === s.key) : undefined
  useEffect(() => { api.differentiators().then(setDiff).catch(() => {}) }, [])
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
        if (scrolled.current !== i) { scrolled.current = i; el.scrollIntoView({ block: 'center', behavior: 'smooth' }) }
        const r = el.getBoundingClientRect(), c = q('.coach')?.getBoundingClientRect()
        if (c && r.width < window.innerWidth * 0.6) {
          const hit = !(r.right < c.left || r.left > c.right || r.bottom < c.top || r.top > c.bottom)
          if (hit && flipped.current !== i) { flipped.current = i; setLeft(x => !x) }
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
    <div className="scrim coach-ov" role="dialog" aria-modal="true" aria-label="WardFlow가 다른 점">
      <div className="ov">
        <div className="coach-h"><span className="mode-tag">시연</span><span className="sub">정리</span><button className="link" onClick={onExit}>끝내기</button></div>
        <h2>WardFlow가 다른 점</h2>
        {!diff ? <div className="empty">불러오는 중</div> : (
          <table className="diff">
            <colgroup><col style={{ width: 36 }} /><col style={{ width: 230 }} /><col /><col style={{ width: 190 }} /><col style={{ width: 170 }} /></colgroup>
            <thead><tr><th></th><th>차별점</th><th>무엇이 다른가</th><th>{diff.note}</th><th>보는 곳</th></tr></thead>
            <tbody>{diff.rows.map((r, k) => (
              <tr key={r.key}><td className="no">{k + 1}</td><td><b>{r.title}</b></td><td className="sub">{r.what}</td>
                <td><b className="v">{r.value}</b><em>{r.value_sub}</em></td><td><span className="tag t-gray">{r.where}</span></td></tr>))}</tbody>
          </table>)}
        <div className="coach-a"><button className="btn" onClick={() => setI(i - 1)}>이전</button><button className="btn" onClick={() => setI(0)}>처음부터</button><button className="btn p" onClick={onExit}>처음 화면</button></div>
      </div>
    </div>
  )
  if (min) return <button className="coach-min" onClick={() => setMin(false)}>{label}</button>
  return (
    <div className={'coach' + (left ? ' left' : '')} role="dialog" aria-label="시연 안내">
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
