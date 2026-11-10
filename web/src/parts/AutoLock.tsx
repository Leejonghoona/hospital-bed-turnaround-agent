import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, type User } from '../api'
import { Logo } from '../icons'

// 자동 잠금: 일정 시간 입력이 없으면 화면을 가리고 본인 비밀번호로 다시 연다.
// EMR 인증 해설서: 일정 시간 미사용 시 잠금(10~60분), 화면보호기는 인정하지 않음 (docs/검토/화면재설계/01). 15분은 설계 가정.
const IDLE_MS = 15 * 60 * 1000
export const lockNow = () => window.dispatchEvent(new Event('wardflow:lock'))

export function AutoLock({ user, onSwitch, children }: { user: User; onSwitch: () => void; children: ReactNode }) {
  const [locked, setLocked] = useState(false)
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const last = useRef(Date.now())
  useEffect(() => {
    const touch = () => { last.current = Date.now() }
    const lock = () => setLocked(true)
    const evs = ['mousemove', 'keydown', 'mousedown', 'touchstart', 'wheel']
    evs.forEach(e => window.addEventListener(e, touch, { passive: true }))
    window.addEventListener('wardflow:lock', lock)
    const t = setInterval(() => { if (Date.now() - last.current > IDLE_MS) setLocked(true) }, 15000)
    return () => { evs.forEach(e => window.removeEventListener(e, touch)); window.removeEventListener('wardflow:lock', lock); clearInterval(t) }
  }, [])
  const unlock = async () => {
    setErr('')
    try { await api.unlock(pw); setPw(''); setLocked(false); last.current = Date.now() }
    catch (e) { setErr(e instanceof ApiError ? e.message : '잠금을 풀 수 없습니다') }
  }
  return (<>
    <div aria-hidden={locked} style={locked ? { filter: 'blur(14px)', pointerEvents: 'none', userSelect: 'none' } : undefined}>{children}</div>
    {locked && (
      <div className="scrim lock" role="dialog" aria-modal="true" aria-label="화면 잠금">
        <div className="lockbox">
          <div className="brand"><Logo /><b>WardFlow</b></div>
          <h2>화면이 잠겼습니다</h2>
          <p className="note">{user.name} ({user.role_label}) 계정입니다. 비밀번호를 입력하면 하던 화면으로 돌아갑니다.</p>
          <div className="field"><label htmlFor="lpw">비밀번호</label>
            <input id="lpw" type="password" autoFocus value={pw} onChange={e => setPw(e.target.value)} onKeyDown={e => e.key === 'Enter' && unlock()} /></div>
          {err && <div className="err">{err}</div>}
          <button className="btn p full" onClick={unlock}>잠금 해제</button>
          <button className="btn full" onClick={onSwitch}>다른 계정으로 로그인</button>
        </div>
      </div>
    )}
  </>)
}
