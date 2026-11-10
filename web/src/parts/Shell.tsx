import { useEffect, useState, type ReactNode } from 'react'
import type { User } from '../api'
import { Icon, Logo } from '../icons'
import { lockNow } from './AutoLock'

const DAYS = ['일', '월', '화', '수', '목', '금', '토']
const fmt = (d: Date) => `${d.getMonth() + 1}월 ${d.getDate()}일 (${DAYS[d.getDay()]}) ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

// 역할 화면 공통 틀: 로고, 화면 메뉴, 현재 시각, 계정 메뉴(다른 계정으로 로그인, 화면 잠금, 로그아웃)
export function Shell({ user, nav, page, onPage, now, onLogout, onSwitch, children }: {
  user: User; nav: [string, string][]; page: string; onPage: (p: string) => void; now?: string
  onLogout: () => void; onSwitch: () => void; children: ReactNode
}) {
  const [menu, setMenu] = useState(false)
  const [base] = useState(() => Date.now())
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 15000); return () => clearInterval(t) }, [])
  const clock = now ? new Date(new Date(now).getTime() + (Date.now() - base)) : null
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <nav className="gnav">{nav.map(([id, label]) => (
          <button key={id} className={page === id ? 'on' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => onPage(id)}>{label}</button>
        ))}</nav>
        <div className="ab-r">
          {clock && <span className="clock">{fmt(clock)}</span>}
          <div className="menu-wrap">
            <button className="user" onClick={() => setMenu(m => !m)} aria-expanded={menu} aria-haspopup="menu"><span className="av">{user.name[0]}</span>
              <span><div className="n">{user.name}</div><div className="r">{user.role_label}</div></span><Icon name="chev" small /></button>
            {menu && (
              <div className="menu" role="menu">
                <div className="who"><b>{user.name}</b><br />{user.role_label}</div>
                <button role="menuitem" onClick={onSwitch}><Icon name="swap" small />다른 계정으로 로그인</button>
                <button role="menuitem" onClick={() => { setMenu(false); lockNow() }}><Icon name="lock" small />화면 잠금</button>
                <button role="menuitem" onClick={onLogout}><Icon name="logout" small />로그아웃</button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="body nosd">{children}</div>
    </div>
  )
}

export function useToast(): [string, (m: string) => void] {
  const [t, set] = useState('')
  useEffect(() => { if (!t) return; const x = setTimeout(() => set(''), 4000); return () => clearTimeout(x) }, [t])
  return [t, set]
}
