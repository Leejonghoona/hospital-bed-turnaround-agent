import { useState } from 'react'
import { api, ApiError, type AiOps, type AutonomyRow } from '../api'
import { Stats } from '../parts/Stats'

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`
const minus = (x: number) => `${Math.round(x * 100)}%`.replace('-', '−')
const TABS = [['overview', '개요'], ['autonomy', '자율 범위'], ['model', '모델과 평가'], ['onboarding', '병원 연동']] as const
type Tab = typeof TABS[number][0]

// AI 운영(의료정보팀): 에이전트가 제대로 도는지 보고 설정한다 (DECISIONS 2026-11-10).
export function AiOpsPage({ d, onChange, toast }: { d: AiOps; onChange: (d: AiOps) => void; toast: (m: string) => void }) {
  const [tab, setTab] = useState<Tab>('overview')
  return (
    <div className="aiops">
      <div className="subtabs" role="tablist" aria-label="AI 운영">
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === 'overview' && <Overview d={d} />}
      {tab === 'autonomy' && <Autonomy d={d} onChange={onChange} toast={toast} />}
      {tab === 'model' && <Model d={d} />}
      {tab === 'onboarding' && <Onboarding d={d} onChange={onChange} toast={toast} />}
    </div>
  )
}

function Overview({ d }: { d: AiOps }) {
  const m = d.measured
  const eb = m.entry_breakdown_realistic ?? m.entry_breakdown  // 운영 현실 기준선(침상 정리 회차·청소 인력 시간대)이 있으면 그것을
  return (<>
    <Stats items={[
      { label: '사람 손 없이 확정', value: `${d.today.no_touch}건`, sub: `오늘 확정 ${d.today.committed}건 중` },
      { label: '막은 판단', value: `${d.blocked.length}건`, sub: '오늘, 실행 전 검사' },
      { label: '안전 위반', value: `${d.today.safety}건`, sub: '오늘, 격리·소독·청소·이중 점유' },
      { label: '에이전트 확정 유형', value: `${d.autonomy.filter(r => r.mode === 'auto').length}개`, sub: `판단 유형 ${d.autonomy.length}개 중` },
    ]} />
    <div className="sec-h"><h3>재생 평가</h3><span className="tag t-gray">{d.note}</span></div>
    <div className="grid3">
      <div className="kpi"><span>사람 손 없이 확정</span><b>{pct(m.no_touch.ratio)}</b><em>확정 {m.no_touch.committed}건 중 {m.no_touch.no_touch}건</em></div>
      <div className="kpi"><span>도착 후 입실까지</span><b>{minus(eb.change)}</b><em>{eb.off_min}분 → {eb.on_min}분, 안내한 수속 시각부터. 도착 때 병상 준비 {Math.round(eb.ready_off * 100)}% → {Math.round(eb.ready_on * 100)}%</em></div>
      <div className="kpi"><span>실행 안전 위반</span><b>{m.safety.violations + m.safety.holdout.violations}건</b><em>검사 {m.safety.checks + m.safety.holdout.checks}회</em></div>
    </div>
    <section className="card blocked">
      <div className="card-h"><h3>막은 판단</h3><span className="n">오늘 {d.blocked.length}건</span>
        <div className="chips">{d.blocked_reasons.map(([k, n]) => <span key={k} className="tag t-amber">{k} {n}</span>)}</div></div>
      {d.blocked.length === 0 ? <p className="note">오늘 막은 판단이 없습니다</p> : (
        <table><colgroup><col style={{ width: 90 }} /><col style={{ width: 200 }} /><col /><col /></colgroup>
          <thead><tr><th>시각</th><th>환자</th><th>에이전트 판단</th><th>막은 이유</th></tr></thead>
          <tbody>{d.blocked.slice(0, 10).map((b, i) => <tr key={i}><td className="mono">{b.time}</td><td>{b.case}</td><td>{b.action}</td><td>{b.reason}</td></tr>)}</tbody></table>)}
    </section>
  </>)
}

function Autonomy({ d, onChange, toast }: { d: AiOps; onChange: (d: AiOps) => void; toast: (m: string) => void }) {
  const [busy, setBusy] = useState('')
  const set = async (r: AutonomyRow, to: 'auto' | 'human') => {
    if (r.mode === to) return
    setBusy(r.unit)
    try { const x = await api.setAutonomy(r.unit, to); onChange({ ...d, autonomy: x.autonomy }); toast(x.message) }
    catch (e) { toast(e instanceof ApiError ? e.message : '바꾸지 못했습니다') } finally { setBusy('') }
  }
  return (
    <section className="card settings">
      <div className="card-h"><h3>판단 유형별 확정 방식</h3></div>
      <dl className="kv rule"><dt>위임 조건</dt><dd>{d.autonomy_rule.promote}</dd><dt>되돌림 권고</dt><dd>{d.autonomy_rule.demote}</dd></dl>
      <div className="opts">
        <div className="opt head"><span>판단 유형</span><span>확정 방식</span><span>사람 결정과 일치</span><span>권고</span></div>
        {d.autonomy.map(r => {
          const canAuto = !r.locked && (r.mode === 'auto' || r.recommendation === 'promote')
          return (
            <div key={r.unit} className="opt">
              <div><b>{r.name}</b><small>{r.what}</small></div>
              <div className="seg2" role="radiogroup" aria-label={`${r.name} 확정 방식`}>
                <button role="radio" aria-checked={r.mode === 'auto'} className={r.mode === 'auto' ? 'on' : ''} disabled={!!busy || !canAuto}
                  title={r.locked ? '사람이 확인해야 하는 유형입니다' : canAuto ? '' : '위임 조건을 아직 채우지 못했습니다'} onClick={() => set(r, 'auto')}>에이전트</button>
                <button role="radio" aria-checked={r.mode === 'human'} className={r.mode === 'human' ? 'on' : ''} disabled={!!busy} onClick={() => set(r, 'human')}>사람 확인</button>
              </div>
              <div className="sub">{r.human_n ? `${r.human_agreed} / ${r.human_n}건` : '—'}</div>
              <div>{r.locked ? <span className="tag t-gray">사람 확인 고정</span> : r.recommendation === 'hold' ? <span className="sub">—</span>
                : <span className="tag t-amber">{r.recommendation === 'promote' ? '에이전트 위임 권고' : '사람 확인 권고'}</span>}</div>
            </div>)
        })}
      </div>
    </section>
  )
}

function Model({ d }: { d: AiOps }) {
  const m = d.model
  return (
    <div className="grid2">
      <section className="card">
        <div className="card-h"><h3>AI가 맡는 일</h3><span className={'tag ' + (m.external ? 't-blue' : 't-gray')}>{m.using}</span></div>
        <table><colgroup><col style={{ width: 150 }} /><col style={{ width: 96 }} /><col /></colgroup>
          <thead><tr><th>일</th><th>지금 담당</th><th>시험 결과</th></tr></thead>
          <tbody>{m.roles.map(r => <tr key={r.job}><td><b>{r.job}</b></td><td>{r.now}</td><td className="sub">{r.test}</td></tr>)}</tbody></table>
        <dl className="kv"><dt>외부 전송</dt><dd>{m.guard}</dd></dl>
      </section>
      <section className="card">
        <div className="card-h"><h3>배포 전 시험</h3><span className="tag t-gray">{m.note}</span></div>
        <table><colgroup><col style={{ width: 130 }} /><col /><col style={{ width: 120 }} /></colgroup>
          <thead><tr><th>시험</th><th>무엇을 보나</th><th>결과</th></tr></thead>
          <tbody>{m.checks.map(e => <tr key={e.name}><td><b>{e.name}</b></td><td className="sub">{e.what}</td><td><b>{e.value}</b><div className="sub">{e.sub}</div></td></tr>)}</tbody></table>
        <dl className="kv"><dt>다시 시험</dt><dd>코드나 규칙이 바뀔 때마다</dd></dl>
      </section>
    </div>
  )
}

function Onboarding({ d, onChange, toast }: { d: AiOps; onChange: (d: AiOps) => void; toast: (m: string) => void }) {
  const ob = d.onboarding
  const approve = async () => {
    try { const x = await api.approveOnboarding(); onChange({ ...d, onboarding: x }); toast('의료정보팀 승인을 남겼습니다') }
    catch (e) { toast(e instanceof ApiError ? e.message : '승인하지 못했습니다') }
  }
  const done = ob.approvals.filter(a => a.done).length
  return (
    <section className="card onboard">
      <div className="card-h"><h3>데이터 연동</h3></div>
      <p className="note">병원 EMR이 내보내는 표의 열과 코드를 WardFlow의 공통 개념 체계(온톨로지)에 연결합니다. 한 번 연결하면 이후 입원과 퇴원 기록은 연동으로 들어옵니다.</p>
      <table><colgroup><col /><col style={{ width: 120 }} /><col style={{ width: 100 }} /><col style={{ width: 100 }} /><col style={{ width: 90 }} /><col style={{ width: 130 }} /><col style={{ width: 160 }} /></colgroup>
        <thead><tr><th>연동 대상</th><th>개념 연결</th><th>확인 질문</th><th>시험 운영</th><th>승인</th><th>상태</th><th></th></tr></thead>
        <tbody><tr>
          <td><b>{ob.hospital}</b><div className="sub">설정판 {ob.version}</div></td>
          <td>{ob.mapped}개</td><td>{ob.asked}개</td>
          <td><span className={'tag ' + (ob.proof.passed ? 't-green' : 't-red')}>{ob.proof.passed ? '통과' : '실패'}</span></td>
          <td>{done} / {ob.approvals.length}</td>
          <td><span className={'tag ' + (ob.status.startsWith('승인됨') ? 't-green' : 't-amber')}>{ob.status}</span></td>
          <td className="acts">{!ob.approvals[0]?.done && <button className="btn sm p" disabled={!ob.proof.passed} onClick={approve}>의료정보팀으로 승인</button>}</td>
        </tr></tbody></table>
      <div className="grid4">
        <div><span>1. 개념 연결</span><b>{ob.mapped}개</b><em>표 {ob.tables}개의 열, 사건, 코드를 공통 개념에 연결</em></div>
        <div><span>2. 확인 질문</span><b>{ob.asked}개</b><em>AI가 확신하지 못한 것만 사람에게 묻습니다{ob.code_master ? `. 병원 코드표로 ${ob.code_master}개 자동 해결` : ''}</em></div>
        <div><span>3. 시험 운영</span><b>{ob.proof.passed ? '통과' : '실패'}</b><em>그 병원 데이터로 하루를 돌려 사건 순서 위반 {ob.proof.order}건, 안전 위반 {ob.proof.safety}건</em></div>
        <div><span>4. 두 사람 승인</span><b>{done} / {ob.approvals.length}</b><em>{ob.approvals.map(a => `${a.name} ${a.done ? '완료' : '대기'}`).join(', ')}</em></div>
      </div>
    </section>
  )
}
