// 선 아이콘(직접 그린 SVG, 외부 의존 없음). 이름은 쓰임새 기준.
const P: Record<string, string> = {
  list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  bed: 'M2 4v16M2 8h18a2 2 0 0 1 2 2v10M2 17h20M6 8v9',
  clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  users: 'M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M13.5 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0M20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.6a3.5 3.5 0 0 1 0 6.8',
  shield: 'M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  file: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5',
  search: 'M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0M20 20l-3.5-3.5',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 0 0 4 0',
  chev: 'M6 9l6 6 6-6',
  sort: 'M8 9l4-4 4 4M8 15l4 4 4-4',
  filter: 'M3 5h18l-7 8v6l-4-2v-4z',
  play: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M10 8.5v7l6-3.5z',
  panel: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2M9 4v16M13 9h4M13 13h4',
  alert: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7.5v5M12 16h.01',
  x: 'M6 6l12 12M18 6L6 18',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  swap: 'M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7',
  back: 'M15 18l-6-6 6-6',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
}

export function Icon({ name, small }: { name: keyof typeof P | string; small?: boolean }) {
  return (
    <svg className={'ico' + (small ? ' s' : '')} viewBox="0 0 24 24" aria-hidden="true">
      <path d={P[name] ?? ''} />
    </svg>
  )
}

export function Logo() {
  return (
    <span className="mark" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <g fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 6v13M3 15h18v4M3 11h5.5a2 2 0 0 1 2 2v2M12.5 15v-3a2 2 0 0 1 2-2H19a2 2 0 0 1 2 2v3" />
        </g>
        <circle cx="19" cy="5" r="2.6" fill="#7FD1FF" />
      </svg>
    </span>
  )
}
