// API 호출. 토큰은 탭을 닫으면 사라지게 sessionStorage에 둔다(공용 단말 고려).
export type User = { user_id: string; name: string; role: string; role_label: string; ward: string | null }
export type Row = {
  id: string; case: string; kind: string; kind_label: string; action: string; due: string; due_note: string
  late: boolean; bed: string; reason: string; status: string; carried: boolean; patient: string; patient_id: string
  age: number | null; sex: string
}
export type Worklist = { now: string; rows: Row[]; counts: Record<string, number> }
export type Patient = {
  case: string; patient: string; patient_id: string; age: number | null; sex: string; department: string
  status: string; bed: string; isolation: string; timeline: { time: string; who: string; what: string; detail: string }[]
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
  patient: (c: string) => call<Patient>(`/api/patients/${encodeURIComponent(c)}`),
}
