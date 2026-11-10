import { useEffect, useState } from 'react'
import { api, ApiError, token, type User } from '../api'
import { Icon, Logo } from '../icons'
import { shiftsFor } from '../shifts'


// 로그인(개인 계정) → 오늘 근무(병동·근무조). 공용 계정은 없다. 실제 인증 수단은 범위 밖.
export function Login({ demoFirst, onDone, onBack }: { demoFirst: boolean; onDone: (u: User) => void; onBack: () => void }) {
  const [accounts, setAccounts] = useState<User[]>([])
  const [uid, setUid] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [user, setUser] = useState<User | null>(null)
  const [ward, setWard] = useState<string | null>(null)  // 병동 계정은 소속 병동으로 고정 (다른 병동 열람 방지)
  const [shift, setShift] = useState<string | null>(null)

  useEffect(() => { api.demoAccounts().then(setAccounts).catch(() => setAccounts([])) }, [])

  const signed = (t: string, u: User) => { token.set(t); setUser(u); setWard(u.ward); setShift(shiftsFor(u.role)[0].id) }
  const submit = async () => {
    setErr('')
    try { const r = await api.login(uid, pw); signed(r.token, r.user) }
    catch (e) { setErr(e instanceof ApiError ? e.message : '로그인할 수 없습니다') }
  }
  const demo = async (id: string) => { const r = await api.demoLogin(id); signed(r.token, r.user) }
  const start = async () => { if (user && shift) { await api.shift(ward, shift); onDone(user) } }

  if (user) {
    const clinical = user.role === 'ward'
    return (
      <div className="auth"><div className="box" style={{ gridTemplateColumns: '1fr', width: 520 }}>
        <div className="pane">
          <div className="brand"><Logo /><b>WardFlow</b></div>
          <h2 style={{ marginTop: 8 }}>오늘 근무</h2>
          <div className="acc" style={{ cursor: 'default' }}><span className="av">{user.name[0]}</span>
            <span><div className="n">{user.name}</div><div className="r">{user.role_label}</div></span></div>
          {clinical && (<>
            <div className="lab">근무 병동</div>
            <div className="seg"><button className="on" disabled>{(ward ?? '').replace('W', '')}병동 (소속)</button></div>
          </>)}
          <div className="lab">근무조</div>
          <div className="seg">{shiftsFor(user.role).map(s => <button key={s.id} className={shift === s.id ? 'on' : ''} onClick={() => setShift(s.id)}>{s.label}</button>)}</div>
          <button className="btn p full" style={{ marginTop: 8 }} onClick={start}>근무 시작</button>
        </div>
      </div></div>
    )
  }

  const list = (
    <div className="pane">
      <h2>데모 계정</h2>
      <p className="note" style={{ marginTop: -6 }}>고르면 바로 로그인합니다. 직접 입력할 때 비밀번호는 demo1234입니다</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {accounts.map(a => (
          <button key={a.user_id} className="acc" onClick={() => demo(a.user_id)}>
            <span className="av">{a.name[0]}</span>
            <span><div className="n">{a.name}</div><div className="r">{a.role_label}</div></span>
          </button>
        ))}
      </div>
    </div>
  )
  const form = (
    <div className="pane">
      <h2>로그인</h2>
      <div className="field"><label htmlFor="uid">사번</label><input id="uid" value={uid} onChange={e => setUid(e.target.value)} autoComplete="username" /></div>
      <div className="field"><label htmlFor="pw">비밀번호</label><input id="pw" type="password" value={pw} onChange={e => setPw(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && submit()} autoComplete="current-password" /></div>
      {err && <div className="err">{err}</div>}
      <button className="btn p full" onClick={submit}>로그인</button>
    </div>
  )
  return (
    <div className="auth">
      <div>
        <button className="btn sm" style={{ marginBottom: 14 }} onClick={onBack}><Icon name="back" small />처음 화면</button>
        <div className="box">{demoFirst ? <>{list}{form}</> : <>{form}{list}</>}</div>
      </div>
    </div>
  )
}
