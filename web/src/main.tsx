import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, token, type User } from './api'
import { Entry } from './pages/Entry'
import { Login } from './pages/Login'
import { Worklist } from './pages/Worklist'
import './styles.css'

type View = { page: 'entry' } | { page: 'login'; demoFirst: boolean } | { page: 'app'; user: User } | { page: 'demo' }

function App() {
  const [view, setView] = useState<View>({ page: 'entry' })
  useEffect(() => {  // 새로고침해도 같은 탭에서는 로그인 유지
    if (token.get()) api.me().then(m => setView({ page: 'app', user: m.user })).catch(() => token.clear())
  }, [])
  const logout = () => { token.clear(); setView({ page: 'entry' }) }

  if (view.page === 'login') return <Login demoFirst={view.demoFirst} onDone={u => setView({ page: 'app', user: u })} onBack={() => setView({ page: 'entry' })} />
  if (view.page === 'app') return <Worklist user={view.user} onLogout={logout} />
  if (view.page === 'demo') return <div className="empty">시연 화면은 P5에서 붙입니다 <button className="btn sm" onClick={() => setView({ page: 'entry' })}>처음 화면</button></div>
  return <Entry onDemo={() => setView({ page: 'demo' })} onOps={() => setView({ page: 'login', demoFirst: true })} onLogin={() => setView({ page: 'login', demoFirst: false })} />
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
