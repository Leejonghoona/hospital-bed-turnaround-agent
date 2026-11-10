import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Admission, type Admissions, type Patient } from '../api'
import { Action } from './Worklist'
import { Stats } from '../parts/Stats'

// 원무 첫 화면 '오늘 입원'(UX 재설계 10): 오늘 들어올 환자 전부. 평소 상태는 칸으로, 예외는 그 행의 배지로. 처리는 옆 패널.
const STAGE_TONE: Record<string, string> = { '입실 완료': 't-green', '호출됨': 't-blue', '취소': 't-gray' }
const BED_TONE: Record<string, string> = { '입실 가능': 'ok', '빈 병상': 'ok', '청소 중': 'wait', '청소 대기': 'wait', '퇴원 진행': 'wait' }
const FILTERS: [string, string][] = [['all', '전체'], ['issue', '확인 필요'], ['contact', '연락 전'], ['nobed', '병상 대기'], ['done', '입실 완료']]

function keep(r: Admission, f: string, q: string) {
  const s = !q || r.patient.includes(q) || r.patient_id.includes(q) || r.bed.includes(q)
  const ok = f === 'all' || (f === 'issue' && r.issues.length > 0) || (f === 'contact' && r.contact === '안내 전')
    || (f === 'nobed' && r.stage === '병상 대기') || (f === 'done' && r.stage === '입실 완료')
  return s && ok
}

export function TodayAdmissions({ q, onPicker, onToast, reload }: { q: string; onPicker: (c: string) => void; onToast: (m: string) => void; reload: number }) {
  const [d, setD] = useState<Admissions | null>(null)
  const [f, setF] = useState('all')
  const [pick, setPick] = useState<string | null>(null)
  const [pt, setPt] = useState<Patient | null>(null)
  const load = useCallback(() => api.admissions().then(x => { setD(x); setPick(p => p && x.rows.some(r => r.case === p) ? p : x.rows[0]?.case ?? null) }), [])
  useEffect(() => { load() }, [load, reload])
  const rows = useMemo(() => (d?.rows ?? []).filter(r => keep(r, f, q)), [d, f, q])
  const cur = d?.rows.find(r => r.case === pick) ?? null
  useEffect(() => { if (cur) api.patient(cur.case).then(setPt).catch(() => setPt(null)); else setPt(null) }, [cur?.case, reload])
  const count = (k: string) => (d?.rows ?? []).filter(r => keep(r, k, '')).length
  const s = d?.summary
  const done = (m: string) => { onToast(m); load() }
  return (<>
    <div className="ph"><h1>오늘 입원</h1></div>
    {s && <Stats items={[
      { label: '오늘 입원', value: s.total, sub: `입실 ${s.arrived}명` },
      { label: '병상 확정', value: s.committed, sub: `에이전트 ${s.by_agent}, 원무팀 ${s.committed - s.by_agent}` },
      { label: '입원 안내', value: s.contacted, sub: `안내 전 ${count('contact')}명` },
      { label: '확인 필요', value: s.issues, tone: s.issues ? 'red' : undefined, sub: '배지가 붙은 환자' },
      { label: '오늘 퇴원', value: s.discharges_today, sub: `지금 빈 병상 ${s.free_beds}개` },
    ]} />}
    <div className="tabs" role="tablist">{FILTERS.map(([k, l]) => (
      <button key={k} role="tab" aria-selected={f === k} className={'tab' + (f === k ? ' on' : '')} onClick={() => setF(k)}>{l} <span className={'n' + (k === 'issue' && count('issue') ? ' red' : '')}>{count(k)}</span></button>))}
    </div>
    <div className="work">
      <section className="grid"><div className="tbody">
        {rows.length === 0 ? <div className="empty">{f === 'issue' ? '확인할 것이 없습니다. 평범한 배정은 에이전트가 처리했습니다' : '해당하는 환자가 없습니다'}</div> : (
          <table>
            <colgroup><col style={{ width: 84 }} /><col style={{ width: 132 }} /><col style={{ width: 104 }} /><col style={{ width: 120 }} /><col style={{ width: 82 }} /><col style={{ width: 150 }} /><col style={{ width: 84 }} /><col /></colgroup>
            <thead><tr><th>수속</th><th>환자</th><th>진료과</th><th>병상</th><th>배정</th><th>연락</th><th>상태</th><th>확인 필요</th></tr></thead>
            <tbody>{rows.map(r => (
              <tr key={r.case} data-case={r.case} className={'row' + (r.case === pick ? ' pick' : '') + (r.stage === '입실 완료' ? ' quiet' : '')} onClick={() => setPick(r.case)} tabIndex={0}>
                <td className="due">{r.checkin}<small>{r.checkin_note}</small></td>
                <td className="pt"><b>{r.patient}</b><span className="id">{r.age}세 {r.sex}</span></td>
                <td className="sub">{r.department}</td>
                <td>{r.bed ? <span className={'bedst ' + (BED_TONE[r.bed_state] ?? '')}><b className="mono">{r.bed}</b><small>{r.bed_state || '재원 중'}</small></span> : <span className="sub">—</span>}</td>
                <td className="sub">{r.by}</td>
                <td className={r.contact === '안내 전' ? '' : 'sub'}>{r.contact}</td>
                <td><span className={'tag ' + (STAGE_TONE[r.stage] ?? 't-gray')}>{r.stage}</span></td>
                <td className="badges">{r.issues.map(i => <span key={i.id} className={'badge' + (i.late ? ' late' : '')}>{i.label}{i.late && ' 지남'}</span>)}</td>
              </tr>))}</tbody>
          </table>)}
      </div></section>
      <aside className="detail" aria-label="상세">
        {!cur || !pt ? <div className="empty">왼쪽에서 환자를 고르세요</div> : (<>
          <div className="dh">
            <div className="top"><span className="nm">{pt.patient}</span><span className="id">{pt.patient_id}</span>
              {cur.stage !== '입실 완료' && cur.stage !== '취소' && <button className="btn sm push" onClick={() => onPicker(cur.case)}>{cur.bed ? '병상 변경' : '병상 직접 지정'}</button>}</div>
            <dl className="facts two">
              <dt>나이 성별</dt><dd>{pt.age}세 {pt.sex}</dd><dt>격리</dt><dd>{pt.isolation}</dd>
              <dt>진료과</dt><dd>{pt.department}</dd><dt>구분</dt><dd>{pt.specialty}</dd>
              <dt>입원 예약</dt><dd>{pt.reserved_at}</dd><dt>희망 병실</dt><dd>{pt.room_pref}</dd>
              <dt>병상</dt><dd className="wide">{pt.bed === '-' ? '배정 전' : `${pt.bed}, ${cur.by} 확정`}</dd>
              <dt>환자</dt><dd>{pt.phone}</dd><dt>보호자</dt><dd>{pt.guardian_phone}</dd>
            </dl>
          </div>
          <div className="db">
            {cur.rows.length === 0 && <p className="note">처리할 것이 없습니다. {cur.stage === '입실 완료' ? '입실을 마쳤습니다.' : cur.contact.startsWith('안내함') ? `환자에게 ${cur.contact.replace('안내함, ', '')}로 안내했습니다.` : ''}</p>}
            {[...cur.rows].sort((a, b) => Number(a.kind === 'contact' || a.kind === 'confirmed') - Number(b.kind === 'contact' || b.kind === 'confirmed')).map(r => (
              <div key={r.id} className={'issue' + (r.kind === 'contact' || r.kind === 'confirmed' ? ' routine' : '')}>
                <div className="lab">{r.kind_label}{r.late && <span className="badge late">마감 {r.due_note === '어제' ? '어제 ' : ''}{r.due} 지남</span>}</div>
                {r.situation && <p className="sit">{r.situation}</p>}
                {r.effect && <p className="eff">{r.effect}</p>}
                <Action r={r} onDone={done} />
              </div>))}
            {pt.notes.length > 0 && (<><div className="lab">특이사항</div>
              <ul className="notes">{pt.notes.slice().reverse().map((n, i) => <li key={i}><p>{n.text}</p><span>{n.who} {n.time}</span></li>)}</ul></>)}
            <div className="lab">경과</div>
            <div className="tl">{pt.timeline.slice().reverse().map((t, i) => (
              <Fragment key={i}><span className="t">{t.time}</span><span className="w">{t.what}{t.detail ? `, ${t.detail}` : ''}</span></Fragment>))}</div>
          </div>
        </>)}
      </aside>
    </div>
  </>)
}
