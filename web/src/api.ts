// API 호출. 토큰은 탭을 닫으면 사라지게 sessionStorage에 둔다(공용 단말 고려).
export type User = { user_id: string; name: string; role: string; role_label: string; ward: string | null }
export type Row = {
  id: string; case: string; kind: string; due_at: string | null; kind_label: string; action: string; due: string; due_note: string
  late: boolean; bed: string; reason: string; status: string; carried: boolean; patient: string; patient_id: string
  age: number | null; sex: string; situation: string; effect: string; choices: { label: string; desc: string }[]; suggest_at: string
}
export type Worklist = { now: string; rows: Row[]; counts: Record<string, number>; groups: Record<string, number> }
export type Patient = {
  case: string; patient: string; patient_id: string; age: number | null; sex: string; department: string
  status: string; bed: string; isolation: string; from_icu: boolean; room_pref: string; reserved_at: string
  phone: string; guardian_phone: string; dept_ward: string; specialty: string; notes: { time: string; who: string; text: string; tag: string }[]; timeline: { time: string; who: string; what: string; detail: string }[]
}

export type Assignment = {
  at: string; time: string; case: string; patient: string; patient_id: string; bed: string; bed_short: string; by: string
  basis: string; runner_up: string; undo_until: string; undo_open: boolean; state: string
}

export type BoardBed = { id: string; label: string; status: string; fit: 'ok' | 'reason' | 'ipc' | 'no'; why: string; mine: boolean }
export type Board = {
  case: string; patient: string; current: string
  need: { sex: string; isolation: string; department: string; dept_wards: string[]; specialty: string; grade_wish: string }
  recommend: { bed: string; label: string; ready: string; basis: string }[]
  wards: { id: string; name: string; specialty: string; rooms: { id: string; label: string; grade: string; sex: string; cohort: string; beds: BoardBed[] }[] }[]
  reasons: { code: string; label: string }[]
}

export type WardRow = {
  case: string; patient: string; age: number | null; sex: string; bed: string; checkin: string; prep: string[]
  front: string; front_detail: string; step: string; hold: string; notified: string; by: string
}
export type WardDischarge = { case: string; patient: string; age: number | null; sex: string; bed: string; step: string; departed: string; flags: string[] }
export type WardIncoming = { ward: string; rows: WardRow[]; reasons: string[] }
export type WardBoard = { ward: string; rooms: { id: string; label: string; grade: string; sex: string; beds: { id: string; label: string; state: string; state_ko: string; detail: string }[] }[] }

export type IpcHome = {
  summary: { decisions: number; first_deadline: string; isolated: number; incoming: number; free_np: number; free_single: number; terminal: number }
  decisions: { exception_id: string; case: string; patient: string; who: string; bed: string; room: string; organism: string; precautions: string
    deadline: string; since: string; situation: string; effect: string; roommates: { bed: string; sex: string; age: string; risk: string[] }[] }[]
  free: { bed: string; ward: string; grade: string; state: string }[]
  isolated: { case: string; patient: string; age: number | null; sex: string; organism: string; precautions: string; status: string; bed: string; room: string; checked: boolean }[]
  terminal: { bed: string; ward: string; departed: string; progress: string; elapsed: string }[]
  contacts: { index: string; room: string; count: number; beds: string[]; time: string }[]
  night: { time: string; what: string; detail: string }[]
  board: { ward: string; rooms: { label: string; grade: string; beds: { bed: string; tag: string; kind: string }[] }[] }[]
  handover: { time: string; who: string; text: string }[]
  decline_reasons: string[]
}
export type AdminSummary = { flow: Record<string, string>; load: Record<string, number>; safety: Record<string, any>[]; locked: { user_id: string; name: string; fails: number }[]; note: string }
export type AutonomyRow = { unit: string; name: string; what: string; mode: 'auto' | 'human'; mode_ko: string; evidence: string; lower95: number; human_n: number; recommendation: 'promote' | 'demote' | 'hold'; recommendation_ko: string; locked: boolean }
export type Onboarding = { hospital: string; tables: number; mapped: number; by_stage: Record<string, number>; asked: number; code_master: number
  proof: { passed: boolean; order: number; safety: number; commits: number; events: number; beds: number }
  approvals: { role: string; name: string; done: boolean; who: string | null }[]; status: string; version: string }
export type AiOps = {
  today: { committed: number; no_touch: number; admissions: number; safety: number; safety_rows: Record<string, any>[] }
  measured: { no_touch: { ratio: number; committed: number; no_touch: number; seed_min: number; seeds: string }
    entry_wait: { seeds: number; off_min: number; on_min: number; change: number }
    safety: { seeds: string; violations: number; checks: number; holdout_seeds: string; holdout: { violations: number; checks: number } }
    autonomy: { days: number; curves: { seed: number; periods: { days: string; ratio: number }[]; promoted_day: Record<string, number>; safety_violations: number }[] } }
  note: string
  blocked: { time: string; case: string; case_id: string; action: string; reason: string }[]; blocked_reasons: [string, number][]
  autonomy: AutonomyRow[]; autonomy_rule: string
  model: { using: string; external: boolean; guard: string; roles: { job: string; who: string; why: string }[]; checks: { name: string; value: string; sub: string }[]; note: string }
  onboarding: Onboarding
}
export type OpsSummary = Omit<AdminSummary, 'locked'> & { unassigned: Record<string, number> }
export type Diff = { key: string; title: string; what: string; value: string; value_sub: string; where: string; here?: boolean }
export type LogRow = { real_at: string; sim_at: string; user_id: string; name: string; ip: string; case: string | null; action: string; detail: string; hash: string }
export type CaseTrail = { case: string; patient: string; status: string; bed: string; trail: { time: string; what: string; detail: string }[] }

export type TourStep = {
  run: string; chapter: string; chapter_title: string; summary: string; step: number; steps: number; done: boolean
  title: string; caption: string; look: string[]; now: string | null; changed: { time: string; actor: string; patient: string; what: string; detail: string }[]
}

export type Admission = {
  case: string; patient: string; patient_id: string; age: number | null; sex: string; department: string; checkin: string; checkin_note: string
  bed: string; bed_state: string; by: string; contact: string; stage: string; issues: { id: string; label: string; late: boolean; due: string }[]; rows: Row[]
}
export type Admissions = { now: string; rows: Admission[]; summary: { total: number; committed: number; by_agent: number; contacted: number; arrived: number; issues: number; discharges_today: number; free_beds: number } }

const KEY = 'wardflow.token'
export const token = {
  get: () => { try { return sessionStorage.getItem(KEY) } catch { return null } },
  set: (t: string) => { try { sessionStorage.setItem(KEY, t) } catch { /* 저장 불가 환경 */ } },
  clear: () => { try { sessionStorage.removeItem(KEY) } catch { /* 저장 불가 환경 */ } },
}

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg) } }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const t = token.get()
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(init?.headers ?? {}) },
  })
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail ?? msg } catch { /* 본문 없음 */ }
    if (r.status === 401 && msg.includes('잠겼')) window.dispatchEvent(new Event('wardflow:lock'))  // 서버가 미사용으로 잠근 세션
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  demoAccounts: () => call<User[]>('/api/demo-accounts'),
  login: (user_id: string, password: string) => call<{ token: string; user: User; screen: string }>('/api/login', { method: 'POST', body: JSON.stringify({ user_id, password }) }),
  tourAs: (uid: string) => call<{ user: User; screen: string }>(`/api/tour/as/${encodeURIComponent(uid)}`, { method: 'POST' }),
  tourLogin: () => call<{ token: string; user: User; tour: boolean }>('/api/tour/login', { method: 'POST' }),
  demoLogin: (user_id: string) => call<{ token: string; user: User; screen: string }>('/api/demo-login', { method: 'POST', body: JSON.stringify({ user_id }) }),
  me: () => call<{ user: User; ward: string | null; shift: string | null; now: string; screen: string }>('/api/me'),
  shift: (ward: string | null, shift: string) => call('/api/shift', { method: 'POST', body: JSON.stringify({ ward, shift }) }),
  logout: () => call('/api/logout', { method: 'POST' }),
  unlock: (password: string) => call('/api/unlock', { method: 'POST', body: JSON.stringify({ password }) }),
  undo: (c: string, reason: string, memo: string) => call<{ status: string }>(`/api/assignments/${encodeURIComponent(c)}/undo`, { method: 'POST', body: JSON.stringify({ reason, memo }) }),
  wardIncoming: () => call<WardIncoming>('/api/ward/incoming'),
  wardDischarges: () => call<{ rows: WardDischarge[] }>('/api/ward/discharges'),
  wardBoard: () => call<WardBoard>('/api/ward/board'),
  wardPrep: (c: string) => call(`/api/ward/prep/${encodeURIComponent(c)}`, { method: 'POST' }),
  wardOpinion: (c: string, reason: string, memo: string) => call(`/api/ward/opinion/${encodeURIComponent(c)}`, { method: 'POST', body: JSON.stringify({ reason, memo }) }),
  ipc: () => call<IpcHome>('/api/ipc'),
  ipcCheck: (c: string) => call(`/api/ipc/check/${encodeURIComponent(c)}`, { method: 'POST' }),
  ipcHandover: (memo: string) => call('/api/ipc/handover', { method: 'POST', body: JSON.stringify({ memo }) }),
  ipcDecide: (id: string, verb: 'approve' | 'decline', reason: string, memo: string) =>
    call<{ status: string }>(`/api/ipc/approvals/${encodeURIComponent(id)}/${verb}`, { method: 'POST', body: JSON.stringify({ reason, memo }) }),
  adminSummary: () => call<AdminSummary>('/api/admin/summary'),
  aiOps: () => call<AiOps>('/api/admin/ai-ops'),
  setAutonomy: (unit: string, to: 'auto' | 'human') => call<{ autonomy: AutonomyRow[]; message: string }>(`/api/admin/autonomy/${encodeURIComponent(unit)}`, { method: 'POST', body: JSON.stringify({ to }) }),
  approveOnboarding: () => call<Onboarding>('/api/admin/onboarding/approve', { method: 'POST' }),
  differentiators: () => call<{ rows: Diff[]; note: string }>('/api/differentiators'),
  opsSummary: () => call<OpsSummary>('/api/ops/summary'),
  accessLog: () => call<{ rows: LogRow[] }>('/api/access-log'),
  verifyLog: () => call<{ ok: boolean }>('/api/access-log/verify'),
  adminCase: (c: string) => call<CaseTrail>(`/api/admin/case/${encodeURIComponent(c)}`),
  unlockAccount: (uid: string) => call(`/api/accounts/${encodeURIComponent(uid)}/unlock`, { method: 'POST' }),
  tourChapters: () => call<{ chapters: { id: string; title: string; summary: string; steps: number }[] }>('/api/tour'),
  tourStart: (chapter: string) => call<TourStep>('/api/tour/start', { method: 'POST', body: JSON.stringify({ chapter }) }),
  tourNext: (run: string) => call<TourStep>(`/api/tour/${encodeURIComponent(run)}/next`, { method: 'POST' }),
  admissions: () => call<Admissions>('/api/admissions'),
  worklist: () => call<Worklist>('/api/worklist'),
  beds: (c: string) => call<Board>(`/api/beds?case=${encodeURIComponent(c)}`),
  assign: (c: string, bed: string, reason: string, memo: string) => call('/api/assign', { method: 'POST', body: JSON.stringify({ case: c, bed, reason, memo }) }),
  assignments: (day?: string) => call<{ now: string; day: string; days: string[]; rows: Assignment[] }>(`/api/assignments${day ? `?day=${day}` : ''}`),
  patient: (c: string) => call<Patient>(`/api/patients/${encodeURIComponent(c)}`),
  contact: (c: string, outcome: 'done' | 'no_answer' | 'postpone' | 'cancel', promised_at: string, memo: string) =>
    call(`/api/contacts/${encodeURIComponent(c)}`, { method: 'POST', body: JSON.stringify({ outcome, promised_at, memo }) }),
  taskDone: (id: string, result: string, memo: string) => call(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST', body: JSON.stringify({ result, memo }) }),
  approval: (id: string, verb: 'approve' | 'decline', reason = '', memo = '') =>
    call<{ status: string; reasons: string[] }>(`/api/approvals/${encodeURIComponent(id)}/${verb}`, { method: 'POST', body: JSON.stringify({ reason, memo }) }),
}
