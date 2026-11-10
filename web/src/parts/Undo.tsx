import { useState } from 'react'
import { api, ApiError } from '../api'

// 에이전트 확정 되돌리기(W3): 사유를 고르고 확인하면 병상을 풀고 다시 찾는다(연락 뒤면 담당 검토와 재연락). 기록에 내 이름과 사유가 남는다.
export const UNDO_REASONS = ['다른 병상이 더 적합', '환자 요청', '병동 사정', '기타']

export function UndoForm({ caseId, until, onDone, onCancel }: { caseId: string; until: string; onDone: (m: string) => void; onCancel?: () => void }) {
  const [reason, setReason] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const need = reason === '기타'
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const d = await api.undo(caseId, reason!, memo)
      onDone(d.status === 'released' ? '되돌렸습니다. 병상을 풀고 다시 찾습니다' : '되돌렸습니다. 이미 연락한 환자라 담당 검토와 재연락으로 넘겼습니다')
    } catch (e) { setErr(e instanceof ApiError ? e.message : '되돌리지 못했습니다') } finally { setBusy(false) }
  }
  return (<>
    <div className="note">{until}까지 되돌릴 수 있습니다.</div>
    <div className="lab">되돌리는 사유</div>
    <div className="seg col">{UNDO_REASONS.map(r => <button key={r} className={reason === r ? 'on' : ''} onClick={() => setReason(r)}>{r}</button>)}</div>
    <div className="field"><label htmlFor="um">메모 {need ? <em className="req">필수</em> : <span className="opt">선택</span>}</label>
      <textarea id="um" rows={2} maxLength={500} value={memo} onChange={e => setMemo(e.target.value)} placeholder="통화 내용, 환자 요청 등" /></div>
    {err && <div className="err">{err}</div>}
    <div className="btns">
      {onCancel ? <button className="btn" onClick={onCancel}>취소</button> : <span />}
      <button className="btn p" disabled={busy || !reason || (need && !memo.trim())} onClick={go}>되돌리기</button>
    </div>
  </>)
}
