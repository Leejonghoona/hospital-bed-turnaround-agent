// 근무조. 간호는 3교대, 원무 입원 창구는 주간(09–18시, 서울대병원 입원 창구 안내, config/operations.yaml office_hours)과 야간 당직(설계 가정).
type Shift = { id: string; label: string; short: string }
const NURSE: Shift[] = [
  { id: 'day', label: '낮 07:00–15:00', short: '낮 근무' },
  { id: 'evening', label: '초번 15:00–23:00', short: '초번 근무' },
  { id: 'night', label: '밤 23:00–07:00', short: '밤 근무' },
]
const OFFICE: Shift[] = [
  { id: 'office', label: '주간 09:00–18:00', short: '주간 근무' },
  { id: 'night_duty', label: '야간 당직 18:00–09:00', short: '야간 당직' },
]
export const shiftsFor = (role: string) => (role === 'ward' ? NURSE : OFFICE)
export const shiftShort = (id: string | null) => [...NURSE, ...OFFICE].find(s => s.id === id)?.short ?? ''
