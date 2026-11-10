import { useState } from 'react'
import { api, ApiError, type AiOps } from '../api'
import { Stats } from '../parts/Stats'

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`
const pct0 = (x: number) => `${Math.round(x * 100)}%`

// AI 운영(의료정보팀): 에이전트가 제대로 도는지. 성과, 막은 판단, 자율 범위, 모델과 평가, 새 병원 설정 (DECISIONS 2026-11-10).
export function AiOpsPage({ d, onChange, toast }: { d: AiOps; onChange: (d: AiOps) => void; toast: (m: string) => void }) {
  const [busy, setBusy] = useState('')
  const m = d.measured
  const autoN = d.autonomy.filter(r => r.mode === 'auto').length
  const setUnit = async (unit: string, to: 'auto' | 'human') => {
    setBusy(unit)
    try { const r = await api.setAutonomy(unit, to); onChange({ ...d, autonomy: r.autonomy }); toast(r.message) }
    catch (e) { toast(e instanceof ApiError ? e.message : '바꾸지 못했습니다') } finally { setBusy('') }
  }
  const approve = async () => {
    try { const ob = await api.approveOnboarding(); onChange({ ...d, onboarding: ob }); toast('의료정보팀 승인을 남겼습니다. 감염관리 책임자 승인이 남았습니다') }
    catch (e) { toast(e instanceof ApiError ? e.message : '승인하지 못했습니다') }
  }
  // 60일 재생에서 기간별 사람 손 없이 확정한 비율(시드 3개 평균)
  const periods = m.autonomy.curves[0].periods.map((p, i) => ({ days: p.days, ratio: m.autonomy.curves.reduce((a, c) => a + c.periods[i].ratio, 0) / m.autonomy.curves.length }))
  const ob = d.onboarding
  return (
    <div className="aiops">
      <Stats items={[
        { label: '오늘 사람 손 없이 확정', value: `${d.today.no_touch}건`, sub: `확정 ${d.today.committed}건 중` },
        { label: '오늘 막은 판단', value: `${d.blocked.length}건`, sub: '실행 전 게이트웨이가 막음' },
        { label: '오늘 안전 위반', value: `${d.today.safety}건`, sub: '격리, 소독, 청소, 이중 점유' },
        { label: '에이전트가 맡은 유형', value: `${autoN}개`, sub: `판단 유형 ${d.autonomy.length}개 중` },
        { label: '새 병원 설정', value: ob.status, sub: ob.hospital },
      ]} />

      <h2 className="sec">에이전트 성과 <span className="tag t-gray">{d.note}</span></h2>
      <div className="kpis">
        <div className="kpi"><span>사람 손 없이 확정</span><b>{pct(m.no_touch.ratio)}</b>
          <em>확정 {m.no_touch.committed}건 중 {m.no_touch.no_touch}건, 시드 {m.no_touch.seeds}, 시드 최저 {pct0(m.no_touch.seed_min)}</em></div>
        <div className="kpi"><span>수속부터 입실까지</span><b>{String(Math.round(m.entry_wait.change * 100)).replace("-", "−")}%</b>
          <em>{m.entry_wait.off_min}분에서 {m.entry_wait.on_min}분으로, 에이전트 없이 돌린 같은 병원과 비교, 시드 {m.entry_wait.seeds}개</em></div>
        <div className="kpi"><span>실행 안전 위반</span><b>{m.safety.violations}건</b>
          <em>검사 {m.safety.checks}회(시드 {m.safety.seeds}), 따로 둔 시드 {m.safety.holdout_seeds}도 {m.safety.holdout.violations}건</em></div>
      </div>

      <h2 className="sec">자율 범위</h2>
      <div className="two-col wide-l">
        <section className="card">
          <table className="auto">
            <colgroup><col style={{ width: 170 }} /><col style={{ width: 92 }} /><col /><col style={{ width: 104 }} /><col style={{ width: 128 }} /></colgroup>
            <thead><tr><th>판단 유형</th><th>지금 확정</th><th>근거</th><th>제안</th><th></th></tr></thead>
            <tbody>{d.autonomy.map(r => (
              <tr key={r.unit}>
                <td><b>{r.name}</b><div className="sub">{r.what}</div></td>
                <td><span className={'tag ' + (r.mode === 'auto' ? 't-blue' : 't-gray')}>{r.mode_ko}</span></td>
                <td className="sub">{r.evidence}</td>
                <td>{r.locked ? <span className="tag t-gray">늘 사람 확인</span> : r.recommendation === 'hold' ? '유지' : <span className="tag t-amber">{r.recommendation_ko}</span>}</td>
                <td className="acts">
                  {!r.locked && r.mode === 'human' && r.recommendation === 'promote' && <button className="btn sm p" disabled={!!busy} onClick={() => setUnit(r.unit, 'auto')}>에이전트에 맡기기</button>}
                  {r.mode === 'auto' && <button className="btn sm" disabled={!!busy} onClick={() => setUnit(r.unit, 'human')}>사람 확인으로</button>}
                </td>
              </tr>))}</tbody>
          </table>
          <p className="note">{d.autonomy_rule} 격리, 1인실 희망처럼 사람이 확인해야 하는 유형은 맡길 수 없습니다.</p>
        </section>
        <section className="card">
          <h3>보수적으로 시작한 병원의 60일</h3>
          <p className="note">처음에는 모든 확정을 사람이 합니다. 기록이 쌓이면 에이전트가 맡을 유형을 제안하고, 승인하면 넓어집니다.</p>
          <div className="bars" role="img" aria-label="기간별 사람 손 없이 확정한 비율">
            {periods.map(p => (
              <div key={p.days} className="bar" title={`${p.days}: ${pct0(p.ratio)}`}>
                <em>{pct0(p.ratio)}</em><i style={{ height: `${Math.max(p.ratio * 100, 1)}%` }} /><span>{p.days}</span></div>))}
          </div>
          <p className="note">확정 중 사람 손 없이 에이전트가 확정한 비율, 시드 3개 평균. 60일 동안 실행 안전 위반 {m.autonomy.curves.reduce((a, c) => a + c.safety_violations, 0)}건. {d.note}.</p>
        </section>
      </div>

      <h2 className="sec">막은 판단 <span className="n">{d.blocked.length}</span></h2>
      <section className="card">
        <p className="note">에이전트가 낸 판단도 실행 직전에 게이트웨이가 다시 검사합니다. 아래는 오늘 실행되지 않은 판단입니다.</p>
        {d.blocked_reasons.length > 0 && <div className="chips">{d.blocked_reasons.map(([k, n]) => <span key={k} className="tag t-amber">{k} {n}건</span>)}</div>}
        {d.blocked.length === 0 ? <p>오늘 막은 판단이 없습니다</p> : (
          <table><thead><tr><th>시각</th><th>환자</th><th>에이전트 판단</th><th>막은 이유</th></tr></thead>
            <tbody>{d.blocked.slice(0, 8).map((b, i) => <tr key={i}><td className="mono">{b.time}</td><td>{b.case}</td><td>{b.action}</td><td>{b.reason}</td></tr>)}</tbody></table>)}
      </section>

      <h2 className="sec">모델과 평가</h2>
      <div className="two-col">
        <section className="card">
          <h3>지금 쓰는 모델 <span className="tag t-green">{d.model.using}</span></h3>
          <table><thead><tr><th>일</th><th>누가</th><th>이유</th></tr></thead>
            <tbody>{d.model.roles.map(r => <tr key={r.job}><td><b>{r.job}</b></td><td>{r.who}</td><td className="sub">{r.why}</td></tr>)}</tbody></table>
          <p className="note">{d.model.guard}</p>
        </section>
        <section className="card">
          <h3>평가</h3>
          <div className="evals">{d.model.checks.map(e => <div key={e.name}><span>{e.name}</span><b>{e.value}</b><em>{e.sub}</em></div>)}</div>
          <p className="note">코드나 정책을 바꾸면 회귀 시나리오와 안전 재생 검사를 다시 돌립니다. {d.model.note}.</p>
        </section>
      </div>

      <h2 className="sec">새 병원 설정</h2>
      <section className="card">
        <h3>{ob.hospital} <span className={'tag ' + (ob.status.startsWith('승인됨') ? 't-green' : 't-amber')}>{ob.status}</span></h3>
        <div className="steps">
          <div><span>1. 내보내기 표 올림</span><b>표 {ob.tables}개</b><em>연결한 항목 {ob.mapped}개</em></div>
          <div><span>2. 모르는 것만 질문</span><b>{ob.asked}개</b><em>{Object.entries(ob.by_stage).map(([k, v]) => `${k} ${v}`).join(', ')}{ob.code_master ? `, 코드표로 ${ob.code_master}개 해결` : ''}</em></div>
          <div><span>3. 시험 운영 검사</span><b>{ob.proof.passed ? '통과' : '실패'}</b><em>그 병원 데이터로 하루를 돌려 안전 위반 {ob.proof.safety}건, 사건 순서 위반 {ob.proof.order}건, 배정 {ob.proof.commits}건</em></div>
          <div><span>4. 두 사람 승인</span><b>{ob.approvals.filter(a => a.done).length} / {ob.approvals.length}</b>
            <em>{ob.approvals.map(a => `${a.name} ${a.done ? '승인' : '대기'}`).join(', ')}</em></div>
        </div>
        <div className="row-end">
          <span className="note">설정판 {ob.version}. 격리와 성별 항목이 있어 감염관리 책임자도 승인해야 운영을 시작합니다.</span>
          {!ob.approvals[0]?.done && <button className="btn p" disabled={!ob.proof.passed} onClick={approve}>의료정보팀으로 승인</button>}
        </div>
      </section>
    </div>
  )
}
