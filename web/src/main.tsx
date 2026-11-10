import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, token, type User } from './api'
import { Entry } from './pages/Entry'
import { Login } from './pages/Login'
import { AutoLock } from './parts/AutoLock'
import { RoleHome } from './pages/RoleHome'
import { WardHome } from './pages/WardHome'
import { Worklist } from './pages/Worklist'
import './styles.css'

type View = { page: 'entry' } | { page: 'login'; demoFirst: boolean } | { page: 'app'; user: User } | { page: 'demo' }

function App() {
  const [view, setView] = useState<View>({ page: 'entry' })
  useEffect(() => {  // 새로고침해도 같은 탭에서는 로그인 유지
    if (token.get()) api.me().then(m => setView({ page: 'app', user: m.user })).catch(() => token.clear())
  }, [])
  const logout = () => { api.logout().catch(() => {}).finally(() => { token.clear(); setView({ page: 'entry' }) }) }

  if (view.page === 'login') return <Login demoFirst={view.demoFirst} onDone={u => setView({ page: 'app', user: u })} onBack={() => setView({ page: 'entry' })} />
  const onSwitch = () => { api.logout().catch(() => {}).finally(() => { token.clear(); setView({ page: 'login', demoFirst: true }) }) }
  // 역할마다 화면이 다르다: 원무팀은 할 일 화면, 다른 팀은 각자의 화면(권한도 서버가 역할로 막는다)
  if (view.page === 'app') return (
    <AutoLock user={view.user} onSwitch={onSwitch}>
      {view.user.role === 'bed_manager' ? <Worklist user={view.user} onLogout={logout} onSwitch={onSwitch} />
        : view.user.role === 'ward' ? <WardHome user={view.user} onLogout={logout} onSwitch={onSwitch} />
          : <RoleHome user={view.user} onLogout={logout} onSwitch={onSwitch} />}
    </AutoLock>)
  if (view.page === 'demo') return <div className="empty">시연 화면을 준비하고 있습니다 <button className="btn sm" onClick={() => setView({ page: 'entry' })}>처음 화면</button></div>
  return <Entry onDemo={() => setView({ page: 'demo' })} onOps={() => setView({ page: 'login', demoFirst: true })} />
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
