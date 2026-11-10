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
  phone: string; guardian_phone: string; dept_ward: string; notes: { time: string; who: string; text: string; tag: string }[]; timeline: { time: string; who: string; what: string; detail: string }[]
}

export type Assignment = {
  at: string; time: string; case: string; patient: string; patient_id: string; bed: string; bed_short: string; by: string
  basis: string; runner_up: string; undo_until: string; undo_open: boolean; state: string
}

export type BoardBed = { id: string; label: string; status: string; fit: 'ok' | 'reason' | 'ipc' | 'no'; why: string; mine: boolean }
export type Board = {
  case: string; patient: string; current: string
  need: { sex: string; isolation: string; department: string; dept_wards: string[]; grade_wish: string }
  recommend: { bed: string; label: string; ready: string; basis: string }[]
  wards: { id: string; name: string; specialty: string; rooms: { id: string; label: string; grade: string; sex: string; cohort: string; beds: BoardBed[] }[] }[]
  reasons: { code: string; label: string }[]
}

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
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  demoAccounts: () => call<User[]>('/api/demo-accounts'),
  login: (user_id: string, password: string) => call<{ token: string; user: User; screen: string }>('/api/login', { method: 'POST', body: JSON.stringify({ user_id, password }) }),
  demoLogin: (user_id: string) => call<{ token: string; user: User; screen: string }>('/api/demo-login', { method: 'POST', body: JSON.stringify({ user_id }) }),
  me: () => call<{ user: User; ward: string | null; shift: string | null; now: string; screen: string }>('/api/me'),
  shift: (ward: string | null, shift: string) => call('/api/shift', { method: 'POST', body: JSON.stringify({ ward, shift }) }),
  worklist: () => call<Worklist>('/api/worklist'),
  beds: (c: string) => call<Board>(`/api/beds?case=${encodeURIComponent(c)}`),
  assign: (c: string, bed: string, reason: string, memo: string) => call('/api/assign', { method: 'POST', body: JSON.stringify({ case: c, bed, reason, memo }) }),
  assignments: () => call<{ now: string; rows: Assignment[] }>('/api/assignments'),
  patient: (c: string) => call<Patient>(`/api/patients/${encodeURIComponent(c)}`),
  contact: (c: string, outcome: 'done' | 'no_answer', promised_at: string, memo: string) =>
    call(`/api/contacts/${encodeURIComponent(c)}`, { method: 'POST', body: JSON.stringify({ outcome, promised_at, memo }) }),
  taskDone: (id: string, result: string, memo: string) => call(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST', body: JSON.stringify({ result, memo }) }),
  approval: (id: string, verb: 'approve' | 'decline', reason = '', memo = '') =>
    call<{ status: string; reasons: string[] }>(`/api/approvals/${encodeURIComponent(id)}/${verb}`, { method: 'POST', body: JSON.stringify({ reason, memo }) }),
}
