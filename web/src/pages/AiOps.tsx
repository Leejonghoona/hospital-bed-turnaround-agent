import { useState } from 'react'
import { api, ApiError, type AiOps, type AutonomyRow } from '../api'
import { Stats } from '../parts/Stats'

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`
const minus = (x: number) => `${Math.round(x * 100)}%`.replace('-', '−')
const TABS = [['overview', '개요'], ['autonomy', '자율 범위'], ['model', '모델과 평가'], ['onboarding', '새 병원 설정']] as const
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
      <div className="kpi"><span>수속부터 입실까지</span><b>{minus(m.entry_wait.change)}</b><em>{m.entry_wait.off_min}분 → {m.entry_wait.on_min}분, 에이전트 미사용 대비</em></div>
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
        <div className="card-h"><h3>모델 구성</h3><span className="tag t-green">{m.using}</span></div>
        <table><colgroup><col style={{ width: 130 }} /><col style={{ width: 110 }} /><col /></colgroup>
          <thead><tr><th>역할</th><th>담당</th><th>상태</th></tr></thead>
          <tbody>{m.roles.map(r => <tr key={r.job}><td><b>{r.job}</b></td><td>{r.who}</td><td className="sub">{r.status}</td></tr>)}</tbody></table>
        <dl className="kv"><dt>외부 전송</dt><dd>{m.guard}</dd></dl>
      </section>
      <section className="card">
        <div className="card-h"><h3>평가</h3><span className="tag t-gray">{m.note}</span></div>
        <table><colgroup><col style={{ width: 130 }} /><col style={{ width: 110 }} /><col /></colgroup>
          <thead><tr><th>항목</th><th>결과</th><th>범위</th></tr></thead>
          <tbody>{m.checks.map(e => <tr key={e.name}><td><b>{e.name}</b></td><td>{e.value}</td><td className="sub">{e.sub}</td></tr>)}</tbody></table>
        <dl className="kv"><dt>재평가</dt><dd>코드나 정책이 바뀔 때마다</dd></dl>
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
    <section className="card">
      <div className="card-h"><h3>설정판</h3></div>
      <table><colgroup><col /><col style={{ width: 100 }} /><col style={{ width: 110 }} /><col style={{ width: 100 }} /><col style={{ width: 140 }} /><col style={{ width: 170 }} /></colgroup>
        <thead><tr><th>병원</th><th>질문</th><th>시험 운영</th><th>승인</th><th>상태</th><th></th></tr></thead>
        <tbody><tr>
          <td><b>{ob.hospital}</b><div className="sub">설정판 {ob.version}</div></td>
          <td>{ob.asked}개</td>
          <td><span className={'tag ' + (ob.proof.passed ? 't-green' : 't-red')}>{ob.proof.passed ? '통과' : '실패'}</span></td>
          <td>{done} / {ob.approvals.length}</td>
          <td><span className={'tag ' + (ob.status.startsWith('승인됨') ? 't-green' : 't-amber')}>{ob.status}</span></td>
          <td className="acts">{!ob.approvals[0]?.done && <button className="btn sm p" disabled={!ob.proof.passed} onClick={approve}>의료정보팀으로 승인</button>}</td>
        </tr></tbody></table>
      <div className="grid4">
        <div><span>표 연결</span><b>{ob.mapped}개 항목</b><em>내보내기 표 {ob.tables}개</em></div>
        <div><span>질문</span><b>{ob.asked}개</b><em>{Object.entries(ob.by_stage).map(([k, v]) => `${k} ${v}`).join(', ')}{ob.code_master ? `, 코드표로 ${ob.code_master}개 해결` : ''}</em></div>
        <div><span>시험 운영</span><b>{ob.proof.passed ? '통과' : '실패'}</b><em>안전 위반 {ob.proof.safety}건, 순서 위반 {ob.proof.order}건, 배정 {ob.proof.commits}건</em></div>
        <div><span>승인</span><b>{done} / {ob.approvals.length}</b><em>{ob.approvals.map(a => `${a.name} ${a.done ? '완료' : '대기'}`).join(', ')}</em></div>
      </div>
    </section>
  )
}
