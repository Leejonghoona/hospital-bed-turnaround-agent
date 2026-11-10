import { useState } from 'react'
import type { User } from '../api'
import { Icon, Logo } from '../icons'
import { lockNow } from '../parts/AutoLock'

// 원무가 아닌 역할의 첫 화면. 역할마다 화면과 권한이 다르다(최소 권한). 병동·감염관리실·관리자 화면은 로드맵 P3·P4에서 만든다.
const NEXT: Record<string, string> = {
  ward: '병동 화면에서는 우리 병동으로 올 환자와 퇴원 진행, 병상 준비를 봅니다.',
  ipc: '감염관리실 화면에서는 격리 환자의 병상 결정과 종말소독 확인을 처리합니다.',
  id_oncall: '감염관리실 화면에서는 격리 환자의 병상 결정과 종말소독 확인을 처리합니다.',
  admin: '관리자 화면에서는 접속기록과 계정, 정책 설정을 봅니다.',
}

export function RoleHome({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: () => void }) {
  const [menu, setMenu] = useState(false)
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand"><Logo /><b>WardFlow</b></div>
        <div className="ab-r">
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
      <div className="empty role-home">
        <h1>{user.role_label} 화면은 준비 중입니다</h1>
        <p>{NEXT[user.role] ?? ''}</p>
        <p className="note">지금은 원무팀 화면만 열려 있습니다. 원무 계정으로 로그인하면 할 일과 배정 기록을 볼 수 있습니다.</p>
      </div>
    </div>
  )
}
