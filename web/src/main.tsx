import { StrictMode, useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, token, type User } from './api'
import { Entry } from './pages/Entry'
import { Login } from './pages/Login'
import { AutoLock } from './parts/AutoLock'
import { Coach } from './parts/Coach'
import { RoleHome } from './pages/RoleHome'
import { WardHome } from './pages/WardHome'
import { IpcHome } from './pages/IpcHome'
import { OpsHome } from './pages/OpsHome'
import { AdminHome } from './pages/AdminHome'
import { Worklist } from './pages/Worklist'
import './styles.css'

type View = { page: 'entry' } | { page: 'login'; demoFirst: boolean } | { page: 'app'; user: User; tour?: boolean } | { page: 'demo' }

function App() {
  const [view, setView] = useState<View>({ page: 'entry' })
  useEffect(() => {  // 새로고침해도 같은 탭에서는 로그인 유지
    if (token.get()) api.me().then(m => setView({ page: 'app', user: m.user })).catch(() => token.clear())
  }, [])
  const tourUser = useCallback((u: User) => setView({ page: 'app', user: u, tour: true }), [])
  const logout = () => { api.logout().catch(() => {}).finally(() => { token.clear(); setView({ page: 'entry' }) }) }

  if (view.page === 'login') return <Login demoFirst={view.demoFirst} onDone={u => setView({ page: 'app', user: u })} onBack={() => setView({ page: 'entry' })} />
  const onSwitch = () => { api.logout().catch(() => {}).finally(() => { token.clear(); setView({ page: 'login', demoFirst: true }) }) }
  // 역할마다 화면이 다르다: 원무팀은 할 일 화면, 다른 팀은 각자의 화면(권한도 서버가 역할로 막는다)
  if (view.page === 'app') return (
    <AutoLock user={view.user} onSwitch={onSwitch}>
      {view.user.role === 'bed_manager' ? <Worklist user={view.user} onLogout={logout} onSwitch={onSwitch} tour={view.tour} />
        : view.user.role === 'ward' ? <WardHome user={view.user} onLogout={logout} onSwitch={onSwitch} />
          : view.user.role === 'ipc' || view.user.role === 'id_oncall' ? <IpcHome user={view.user} onLogout={logout} onSwitch={onSwitch} />
            : view.user.role === 'bed_lead' ? <OpsHome user={view.user} onLogout={logout} onSwitch={onSwitch} />
            : view.user.role === 'admin' ? <AdminHome user={view.user} onLogout={logout} onSwitch={onSwitch} />
              : <RoleHome user={view.user} onLogout={logout} onSwitch={onSwitch} />}
      {view.tour && <Coach user={view.user} onUser={tourUser} onExit={logout} />}
    </AutoLock>)
  if (view.page === 'demo') return <div className="empty">시연용 병원을 준비하고 있습니다</div>
  const startTour = () => {
    setView({ page: 'demo' })
    api.tourLogin().then(r => { token.set(r.token); setView({ page: 'app', user: r.user, tour: true }) }).catch(() => setView({ page: 'entry' }))
  }
  return <Entry onDemo={startTour} onOps={() => setView({ page: 'login', demoFirst: true })} />
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
