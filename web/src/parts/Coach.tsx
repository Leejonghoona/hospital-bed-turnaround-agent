import { useEffect, useRef, useState } from 'react'
import { api, type Diff, type User } from '../api'

// 시연: 구현된 실제 화면을 차례로 보여 주며 차별점을 하나씩 짚는다("차별점 1/7"). 짚는 곳은 테두리가 반짝인다.
// 해 볼 수 있는 단계는 사용자가 직접 하면 다음으로 넘어간다. 다른 팀 화면이 필요하면 시연 세션이 그 팀 계정으로 바뀐다(같은 시연용 합성 병원).
// 근거: NN/g "Tutorials … are quickly forgotten", 실제 화면에서 과업을 직접 하게 하는 방식 권장(docs/화면재설계/10_UX_재설계.md)
type Step = { diff: number; title: string; body: string; as: string; target?: () => Element | null; done?: () => boolean; button?: string; summary?: boolean }

const DIFFS = ['평범한 입원은 에이전트가 확정', '실행 직전에 다시 검사', '늦게 온 안전 정보도 되돌린다', '병상을 먼저 만든다',
  '맡기는 범위를 기록으로 넓힌다', '새 병원은 데이터로 세팅', '모델은 바꿔 끼우고 시험으로 고른다']

const q = (sel: string) => document.querySelector(sel)
const toast = (re: RegExp) => re.test(q('.toast')?.textContent ?? '')
const byText = (sel: string, re: RegExp) => [...document.querySelectorAll(sel)].find(e => re.test(e.textContent ?? '')) ?? null
// 구역 제목 바로 아래 첫 카드(두 칸 배치면 왼쪽 카드). 칸 묶음에 테두리를 두면 위아래 줄만 보여 카드에 둔다
const secCard = (h: RegExp) => { const n = byText('h2.sec', h)?.nextElementSibling ?? null; return n && !n.classList.contains('card') ? n.querySelector('.card') : n }
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
const lateRow = () => byText('tr.row', /병상 결정, 마감/)

const ADM = 'adm-kim', IPC = 'ipc-lee', MI = 'admin-jung'
const STEPS: Step[] = [
  { diff: 1, as: ADM, title: '오늘 입원의 대부분은 에이전트가 확정', body: '원무팀의 첫 화면입니다. 반짝이는 "병상 확정"에서 오늘 확정된 병상 가운데 에이전트가 사람 손 없이 확정한 수를 봅니다. 사람은 통보받고 되돌릴 수 있습니다.',
    target: () => byText('.stat', /병상 확정/), button: '다음' },
  { diff: 1, as: ADM, title: '평범한 입원은 배지가 없다', body: '반짝이는 환자를 눌러 보세요. 배지가 없는 이 환자는 에이전트가 조건에 맞는 병상을 확정하고 병동에 알렸습니다.', target: plainRow,
    done: () => !!plainRow()?.classList.contains('pick') },
  { diff: 1, as: ADM, title: '사람은 환자 연락만', body: '환자에게 수속 시각을 안내했다고 기록해 보세요. 반짝이는 "연락함으로 기록"을 누릅니다.', target: () => byText('aside.detail button', /연락함으로 기록/),
    done: () => toast(/입원 안내를 기록/) },
  { diff: 2, as: ADM, title: '판단이 필요한 일만 배지로', body: '배지가 붙은 환자는 사람이 판단할 일입니다. 반짝이는 "재연락" 환자를 눌러 보세요.', target: recontactRow,
    done: () => !!recontactRow()?.classList.contains('pick') },
  { diff: 2, as: ADM, title: '승인해도 실행 직전에 다시 검사', body: '에이전트가 재연락을 제안했습니다. 반짝이는 "승인"을 누르면, 실행 직전에 게이트웨이가 병상과 안전 조건을 다시 확인한 뒤에야 실행합니다.',
    target: () => { const box = byText('aside.detail .issue', /재연락/); return box ? byText('aside.detail .issue button', /^승인$/) : null },
    done: () => toast(/다시 확인/) },
  { diff: 2, as: ADM, title: '확정 근거는 배정 기록에', body: '반짝이는 "배정 기록"을 누르면 누가 어떤 이유로 확정했는지, 언제까지 되돌릴 수 있는지 보입니다.', target: () => byText('.gnav button', /배정 기록/),
    done: () => /배정 기록/.test(q('.ph h1')?.textContent ?? '') },
  { diff: 3, as: IPC, title: '확정 뒤에 격리 오더가 났다', body: '감염관리실 화면입니다. 병상이 확정된 뒤 MRSA가 검출돼 접촉주의 오더가 났습니다. 시스템은 환자 호출을 멈추고 반짝이는 환자에 "병상 결정" 배지를 달아 감염관리실에 올렸습니다.',
    target: lateRow, button: '다음' },
  { diff: 3, as: IPC, title: '사람이 병상을 정한다', body: '동실자와 취약 요인, 빈 격리 병상을 보고 감염관리실이 결정합니다. 늦게 온 안전 정보도 이미 내린 결정을 되돌립니다.',
    target: () => byText('aside.detail .issue', /늦은 격리 병상 결정/), button: '다음' },
  { diff: 4, as: MI, title: '퇴실 즉시 청소 요청, 빈 병상이 일찍 생긴다', body: '의료정보팀의 AI 운영 화면입니다. 에이전트는 환자가 퇴실하는 순간 청소를 요청하고 준비를 재촉합니다. 에이전트 없이 돌린 같은 병원과 비교한 결과입니다(합성 데이터 기준 추정).',
    target: () => byText('.kpi', /수속부터 입실까지/), button: '다음' },
  { diff: 5, as: MI, title: '맡기는 범위는 기록이 정한다', body: '판단 유형마다 지금 누가 확정하는지, 사람 결정과 얼마나 일치했는지 보입니다. 충분히 일치하면 맡기기를 제안하고, 어긋나면 사람 확인으로 되돌립니다. 격리 같은 유형은 늘 사람이 확인합니다.',
    target: () => secCard(/자율 범위/), button: '다음' },
  { diff: 6, as: MI, title: '새 병원은 데이터로 세팅', body: '병원 내보내기 표를 올리면 모르는 것만 묻고, 그 병원 데이터로 하루를 시험 운영한 뒤 의료정보팀과 감염관리 책임자가 승인합니다.',
    target: () => secCard(/새 병원 설정/), button: '다음' },
  { diff: 7, as: MI, title: '모델은 바꿔 끼우고 시험으로 고른다', body: '배정과 숫자는 결정적 코드가 합니다. 모델은 설명과 추출만 맡고, 시험 기준을 넘지 못한 모델은 쓰지 않습니다. 기본값은 외부 전송이 없는 결정적 규칙입니다.',
    target: () => secCard(/모델과 평가/), button: '다음' },
  { diff: 0, as: MI, title: '한눈에 정리', body: '', summary: true },
]

export function Coach({ user, onUser, onExit }: { user: User; onUser: (u: User) => void; onExit: () => void }) {
  const [i, setI] = useState(0)
  const [min, setMin] = useState(false)
  const [diff, setDiff] = useState<{ rows: Diff[]; note: string } | null>(null)
  const scrolled = useRef(-1)
  const s = STEPS[i]
  useEffect(() => { api.differentiators().then(setDiff).catch(() => {}) }, [])
  useEffect(() => {  // 이 단계가 보여 줄 팀 화면으로
    if (s.as !== user.user_id) api.tourAs(s.as).then(r => onUser(r.user)).catch(() => {})
  }, [s.as, user.user_id, onUser])
  useEffect(() => {
    const mark = () => {
      const el = s.target ? s.target() : null
      document.querySelectorAll('.coach-pulse').forEach(e => { if (e !== el) e.classList.remove('coach-pulse') })
      if (el) {
        el.classList.add('coach-pulse')
        if (scrolled.current !== i) { scrolled.current = i; el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }
      }
      if (s.done && s.done()) setI(x => Math.min(x + 1, STEPS.length - 1))
    }
    mark()
    const t = setInterval(mark, 400)
    return () => { clearInterval(t); document.querySelectorAll('.coach-pulse').forEach(e => e.classList.remove('coach-pulse')) }
  }, [i, s])
  const head = s.diff ? <><b className="dnum">차별점 {s.diff}/{DIFFS.length}</b><span className="sub">{DIFFS[s.diff - 1]}</span></> : <b className="dnum">정리</b>
  if (s.summary) return (
    <div className="scrim coach-ov" role="dialog" aria-modal="true" aria-label="WardFlow가 다른 점">
      <div className="ov">
        <div className="coach-h"><span className="mode-tag">시연</span><span className="sub">한눈에 정리</span><button className="link" onClick={onExit}>끝내기</button></div>
        <h2>WardFlow가 다른 점</h2>
        <p className="lead">퇴원부터 다음 환자 입실까지, 판단은 에이전트가 하고 실행은 검사하는 코드가 맡습니다. 사람은 책임질 순간에만 개입합니다.</p>
        {!diff ? <div className="empty">불러오는 중</div> : (
          <table className="diff">
            <colgroup><col style={{ width: 36 }} /><col style={{ width: 230 }} /><col /><col style={{ width: 190 }} /><col style={{ width: 150 }} /></colgroup>
            <thead><tr><th></th><th>차별점</th><th>무엇이 다른가</th><th>{diff.note}</th><th>보는 곳</th></tr></thead>
            <tbody>{diff.rows.map((r, n) => (
              <tr key={r.key}><td className="no">{n + 1}</td><td><b>{r.title}</b></td><td className="sub">{r.what}</td>
                <td><b className="v">{r.value}</b><em>{r.value_sub}</em></td><td><span className="tag t-gray">{r.where}</span></td></tr>))}</tbody>
          </table>)}
        <div className="coach-a"><button className="btn" onClick={() => setI(i - 1)}>이전</button><button className="btn" onClick={() => setI(0)}>처음부터</button><button className="btn p" onClick={onExit}>처음 화면</button></div>
      </div>
    </div>
  )
  if (min) return <button className="coach-min" onClick={() => setMin(false)}>차별점 {s.diff}/{DIFFS.length}</button>
  return (
    <div className="coach" role="dialog" aria-label="시연 안내">
      <div className="coach-h"><span className="mode-tag">시연</span>
        <button className="link" onClick={() => setMin(true)}>접기</button><button className="link" onClick={onExit}>끝내기</button></div>
      <div className="coach-d">{head}</div>
      <h3>{s.title}</h3>
      <p>{s.body}</p>
      <div className="coach-a">
        {i > 0 && <button className="btn sm" onClick={() => setI(i - 1)}>이전</button>}
        {s.button && <button className="btn sm p" onClick={() => setI(i + 1)}>{s.button}</button>}
        {s.done && <button className="btn sm" onClick={() => setI(i + 1)}>건너뛰기</button>}
      </div>
    </div>
  )
}
