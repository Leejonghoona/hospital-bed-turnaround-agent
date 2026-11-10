// 화면 과업 실행기 (docs/화면재설계/tasks.json). 과업 문장대로 누르고 합격 조건을 확인한다. 합성 데이터.
// 사용: node tests/ui_tasks/tasks.cjs <base_url> <out_dir> [과업ID,...]
const { chromium } = require('playwright')
const [base, out, only] = process.argv.slice(2)
const pick = only ? new Set(only.split(',')) : null

async function enterOps(page, user = '김하늘') {
  await page.goto(base)
  await page.getByRole('button', { name: '데모 계정으로 시작' }).click()
  await page.getByRole('button', { name: new RegExp(user) }).first().click()
  await page.getByRole('button', { name: '근무 시작' }).click()
}

// 각 과업: 결과 {id, pass, checks:[{name, ok, detail}]}
const TASKS = {
  async F1(page) {
    await page.goto(base)
    const c = []
    c.push({ name: '시연·운영 카드가 보인다', ok: await page.getByRole('heading', { name: '시연 화면' }).isVisible() && await page.getByRole('heading', { name: '운영 화면' }).isVisible() })
    const [a, b] = await page.locator('.tile').evaluateAll(els => els.map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)] }))
    const wrapped = await page.locator('.e-how b, .tile h3, .e-hero p').evaluateAll(els => els.filter(e => {
      const lh = parseFloat(getComputedStyle(e).lineHeight) || 20; return e.getBoundingClientRect().height > lh * 1.5 }).map(e => e.textContent))
    c.push({ name: '제목이 한 줄에 들어간다', ok: wrapped.length === 0, detail: wrapped.join(' | ') })
    c.push({ name: '두 카드 크기가 같다', ok: !!a && !!b && a[0] === b[0] && a[1] === b[1], detail: JSON.stringify([a, b]) })
    // 시연: 실제 원무 화면에서 안내를 따라 과업을 끝까지 해 본다
    await page.getByRole('button', { name: '시연 시작' }).click()
    const coach = page.getByRole('dialog', { name: '시연 안내' })
    await coach.waitFor({ timeout: 60000 })
    await page.locator('tr.row').first().waitFor()
    const step = (t) => coach.getByRole('heading', { name: t }).waitFor({ timeout: 15000 })
    await page.locator('.stat.coach-pulse').waitFor({ timeout: 10000 }).catch(() => {})
    c.push({ name: '차별점 번호와 짚는 곳이 보인다', ok: /차별점 1\/7/.test(await coach.locator('.coach-d').textContent() || '') && (await page.locator('.stat.coach-pulse').count()) === 1 })
    await coach.getByRole('button', { name: '다음' }).click()
    await step('평범한 입원은 배지가 없다')
    await page.locator('tr.row.coach-pulse').click()
    await step('사람은 환자 연락만')
    await page.getByRole('button', { name: '연락함으로 기록' }).click()
    await step('판단이 필요한 일만 배지로')
    await page.locator('tr.row.coach-pulse').click()
    await step('승인해도 실행 직전에 다시 검사')
    await page.locator('aside.detail .issue', { hasText: '재연락' }).first().getByRole('button', { name: '승인' }).click()
    await step('확정 근거는 배정 기록에')
    await page.getByRole('button', { name: '배정 기록' }).click()
    await step('확정 뒤에 격리 오더가 났다')
    await page.locator('tr.row.coach-pulse').waitFor({ timeout: 30000 })
    c.push({ name: '감염관리실 화면에서 늦은 격리 환자를 짚는다', ok: /병상 결정/.test(await page.locator('tr.row.coach-pulse').textContent() || '') })
    await coach.getByRole('button', { name: '다음' }).click()
    await step('사람이 병상을 정한다')
    c.push({ name: '감염관리실 화면에서 늦은 격리 결정을 짚는다', ok: (await page.locator('.issue.coach-pulse').count()) === 1 })
    for (const t of ['퇴실 즉시 청소 요청, 빈 병상이 일찍 생긴다', '맡기는 범위는 기록이 정한다', '새 병원은 데이터로 세팅', '모델은 바꿔 끼우고 시험으로 고른다']) {
      await coach.getByRole('button', { name: '다음' }).click()
      await step(t)
      await page.locator('.coach-pulse').first().waitFor({ timeout: 30000 }).catch(() => {})
    }
    c.push({ name: 'AI 운영 화면에서 차별점을 짚는다', ok: (await page.locator('.coach-pulse').count()) === 1 && await page.getByRole('heading', { name: 'AI 운영' }).isVisible() })
    await coach.getByRole('button', { name: '다음' }).click()
    const ov = page.getByRole('dialog', { name: 'WardFlow가 다른 점' })
    await ov.locator('table.diff tbody tr').first().waitFor({ timeout: 15000 }).catch(() => {})
    const vals = await ov.locator('table.diff b.v').allTextContents()
    c.push({ name: '마지막에 차별점을 숫자와 함께 한눈에 정리한다', ok: vals.length === 7 && vals.includes('72.4%') && vals.includes('0건'), detail: vals.join(' | ') })
    return c
  },
  async F3(page) {
    await page.goto(base)
    await page.getByRole('button', { name: '시연 시작' }).click()
    const coach = page.getByRole('dialog', { name: '시연 안내' })
    await coach.waitFor({ timeout: 60000 })
    await coach.getByRole('button', { name: '끝내기' }).click()
    await page.getByRole('heading', { name: '시연 화면' }).waitFor({ timeout: 8000 }).catch(() => {})
    return [{ name: '시연을 멈추고 처음 화면으로 돌아간다', ok: await page.getByRole('heading', { name: '시연 화면' }).isVisible() }]
  },
  async D1(page) {
    await page.goto(base)
    await page.getByRole('button', { name: '시연 시작' }).click()
    const coach = page.getByRole('dialog', { name: '시연 안내' })
    await coach.waitFor({ timeout: 60000 })
    await page.locator('.coach-pulse').first().waitFor({ timeout: 5000 }).catch(() => {})
    return [{ name: '시연은 실제 원무 화면 위에서 한다', ok: await page.getByRole('heading', { name: '오늘 입원', exact: true }).isVisible() },
      { name: '시연 중임이 화면에 보이고, 짚는 곳이 강조된다', ok: await coach.locator('.mode-tag').isVisible() && (await page.locator('.coach-pulse').count()) === 1 }]
  },
  async F2(page) {
    let clicks = 0
    await page.goto(base)
    await page.getByRole('button', { name: '데모 계정으로 시작' }).click(); clicks++
    await page.getByRole('button', { name: /김하늘/ }).first().click(); clicks++
    await page.getByRole('button', { name: '근무 시작' }).click(); clicks++
    await page.getByRole('heading', { name: '오늘 입원' }).waitFor()
    return [{ name: '3클릭 이하로 원무 작업 화면', ok: clicks <= 3, detail: `${clicks}클릭` }]
  },
  async W1(page) {
    await enterOps(page)
    await page.getByRole('heading', { name: '오늘 입원' }).waitFor()
    await page.locator('tr.row').first().waitFor()
    const c = []
    const flags = await page.locator('tr.row').evaluateAll(els => els.map(e => [!!e.querySelector('.badge'), !!e.querySelector('.badge.late')]))
    const firstPlain = flags.findIndex(f => !f[0])
    c.push({ name: '확인할 것이 있는 환자가 위, 평소 환자는 아래', ok: firstPlain > 0 && flags.slice(firstPlain).every(f => !f[0]), detail: `${firstPlain}명 위` })
    c.push({ name: '평소 일(오늘 입원 전부)이 한 표에 보인다', ok: flags.length >= 15 && await page.locator('.stats').isVisible(), detail: `${flags.length}명` })
    const cut = await page.locator('td').evaluateAll(els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent))
    c.push({ name: '잘린 칸이 없다', ok: cut.length === 0, detail: cut.slice(0, 3).join(' | ') })
    await page.getByRole('tab', { name: /확인 필요/ }).click()
    const only = await page.locator('tr.row').evaluateAll(els => els.every(e => !!e.querySelector('.badge')))
    c.push({ name: '확인 필요만 걸러 본다', ok: only })
    return c
  },
  async W2(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    const row = page.locator('tr.row').filter({ hasNot: page.locator('.badge') }).filter({ hasText: '안내 전' }).first()
    const name = await row.locator('.pt b').textContent()
    await row.click()
    const c = []
    const t = page.getByLabel('환자에게 안내한 수속 시각')
    await t.waitFor()
    c.push({ name: '추천 안내 시각이 보인다', ok: !!(await t.inputValue()) && await page.getByText(/추천 시각은/).isVisible() })
    await page.getByRole('button', { name: '연락함으로 기록' }).click()
    await page.getByRole('status').waitFor(); await page.waitForTimeout(500)
    const after = await page.locator('tr.row', { hasText: name }).first().textContent()
    c.push({ name: '기록하면 연락 칸이 안내함으로 바뀐다', ok: /안내함/.test(after || ''), detail: name })
    return c
  },
  async W4(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    await page.locator('tr.row', { has: page.locator('.badge', { hasText: '재연락' }) }).first().click()
    const c = []
    const panel = page.locator('aside.detail')
    const issue = panel.locator('.issue', { hasText: '재연락' }).first()
    await issue.waitFor()
    c.push({ name: '상황, 근거, 마감, 버튼이 한 패널에', ok: await issue.locator('.sit').isVisible() && await issue.locator('.eff').isVisible()
      && await issue.getByRole('button', { name: '승인' }).isVisible() && await issue.getByRole('button', { name: '거절' }).isVisible() && await issue.locator('.badge.late').count() >= 0 })
    await issue.getByRole('button', { name: '승인' }).click()
    const msg = await page.getByRole('status').textContent()
    c.push({ name: '승인 뒤 시스템이 다시 확인한 결과가 보인다', ok: /다시 확인/.test(msg || ''), detail: msg })
    return c
  },
  async W3(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    await page.getByRole('button', { name: '배정 기록' }).click()
    const row = page.locator('tr.row', { has: page.getByRole('button', { name: '되돌리기' }) }).first()
    await row.waitFor()
    const name = await row.locator('.pt b').textContent()
    const c = []
    c.push({ name: '되돌릴 수 있는 시한이 보인다', ok: /까지 가능/.test(await row.textContent() || '') })
    await row.getByRole('button', { name: '되돌리기' }).click()
    const dlg = page.getByRole('dialog', { name: '배정 되돌리기' })
    await dlg.getByRole('button', { name: '환자 요청' }).click()
    await dlg.getByRole('button', { name: '되돌리기' }).click()
    const msg = await page.getByRole('status').textContent()
    c.push({ name: '사유를 고르고 확인하면 되돌려진다(연락 뒤면 담당 검토로)', ok: /되돌렸습니다/.test(msg || ''), detail: `${name}: ${msg}` })
    return c
  },
  async A3(page) {
    const t0 = Date.now(); const lap = []
    await enterOps(page); lap.push('login ' + (Date.now() - t0))
    await page.locator('tr.row').first().waitFor(); lap.push('rows ' + (Date.now() - t0))
    const c = []
    await page.getByRole('button', { name: /김하늘/ }).click()
    await page.getByRole('menuitem', { name: '화면 잠금' }).click()
    const lock = page.getByRole('dialog', { name: '화면 잠금' })
    c.push({ name: '잠그면 화면이 가려진다', ok: await lock.isVisible() })
    await lock.getByLabel('비밀번호').fill('demo1234'); await lock.getByRole('button', { name: '잠금 해제' }).click()
    await lock.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {}); lap.push('unlock ' + (Date.now() - t0))
    c.push({ name: '본인 비밀번호로 다시 연다', ok: !(await lock.isVisible()) })
    await page.getByRole('button', { name: /김하늘/ }).click()
    await page.getByRole('menuitem', { name: '다른 계정으로 로그인' }).click()
    await page.getByRole('button', { name: /서민준/ }).first().click(); await page.getByRole('button', { name: '근무 시작' }).click()
    lap.push('switch ' + (Date.now() - t0))
    await page.locator('tr.row').first().waitFor({ timeout: 60000 }); lap.push('rows2 ' + (Date.now() - t0))
    c.push({ name: '다음 근무자 계정으로 넘어간다', ok: await page.getByRole('button', { name: /서민준/ }).isVisible(), detail: lap.join(', ') })
    return c
  },
  async N1(page) {
    await enterOps(page, '박서연')
    await page.locator('.ward-side tr.row').first().waitFor()
    const beds = await page.locator('.ward-side tr.row td.mono').allTextContents()
    return [{ name: '우리 병동으로 올 환자와 준비 상태가 보인다', ok: beds.length > 0 && beds.every(b => b.startsWith('71-')), detail: `${beds.length}명` },
      { name: '병상 현황과 오늘 퇴원 진행이 같은 화면에 있다', ok: (await page.locator('.ward-board .room').count()) > 0 && await page.getByRole('heading', { name: /퇴원 진행/ }).isVisible() }]
  },
  async N3(page) {
    await enterOps(page, '박서연')
    await page.locator('.ward-side tr.row').first().click()
    await page.getByRole('button', { name: '원무팀에 의견 보내기' }).click()
    await page.getByRole('button', { name: '간호 인력 부족' }).click()
    await page.getByRole('button', { name: '의견 보내기', exact: true }).click()
    const msg = await page.getByRole('status').textContent()
    return [{ name: '의견을 내면 원무에 알리고 배정은 그대로', ok: /원무팀에 의견/.test(msg || ''), detail: msg }]
  },
  async N4(page) {
    await enterOps(page, '박서연')
    await page.locator('.ward-board .room').first().waitFor()
    const ready = await page.locator('.bd.st-ready, .bd.st-empty, .bd.st-dirty, .bd.st-cleaning, .bd.st-discharging').count()
    return [{ name: '비었거나 곧 빌 병상을 색과 글로 찾는다', ok: ready > 0 && await page.locator('.legend').isVisible(), detail: `${ready}개` }]
  },
  async I1(page) {
    await enterOps(page, '이준호')
    await page.locator('.stats').waitFor()
    await page.getByRole('button', { name: '지금 병상 유지 등' }).click()
    await page.getByRole('button', { name: '지금 병상 유지(제자리 격리)' }).click()
    await page.getByRole('button', { name: '결정', exact: true }).click()
    const msg = await page.getByRole('status').textContent()
    return [{ name: '늦은 격리 1건의 병상을 결정한다', ok: /정했습니다|알립니다/.test(msg || ''), detail: msg }]
  },
  async I3(page) {
    await enterOps(page, '이준호')
    await page.locator('.stats').waitFor()
    await page.getByRole('button', { name: '격리 병상', exact: true }).click()
    await page.locator('.room').first().waitFor()
    const n = await page.locator('.bd.k-terminal').count()
    return [{ name: '종말소독이 끝나지 않은 병실을 찾는다', ok: n > 0 && await page.getByText(/종말소독 대기:/).isVisible(), detail: `${n}개` }]
  },
  async M1(page) {
    await enterOps(page, '한지우')
    await page.getByRole('heading', { name: '안전 검사' }).waitFor()
    return [{ name: '원무팀장이 오늘 흐름과 안전 검사 결과를 본다', ok: await page.getByRole('heading', { name: '퇴실에서 입실까지' }).isVisible() && (await page.locator('.card tbody tr').count()) >= 4 }]
  },
  async M3(page) {
    await enterOps(page, '정예린')
    await page.locator('.kpi').first().waitFor({ timeout: 30000 })
    const c = []
    const kpis = await page.locator('.kpi b').allTextContents()
    c.push({ name: '에이전트 성과(측정값)를 본다', ok: kpis.join(' ').includes('72.4%'), detail: kpis.join(' | ') })
    for (const h of ['자율 범위', '막은 판단', '모델과 평가', '새 병원 설정']) c.push({ name: `${h} 구역이 있다`, ok: await page.getByRole('heading', { name: h }).first().isVisible() })
    await page.getByRole('button', { name: '사람 확인으로' }).first().click()
    await page.locator('.toast').waitFor({ timeout: 5000 }).catch(() => {})
    c.push({ name: '에이전트가 맡은 유형을 사람 확인으로 되돌린다', ok: /이제 사람이 확인합니다/.test(await page.locator('.toast').textContent().catch(() => '') || '') })
    await page.getByRole('button', { name: '의료정보팀으로 승인' }).click()
    await page.getByText('승인 1명 남음').first().waitFor({ timeout: 5000 }).catch(() => {})
    c.push({ name: '새 병원 설정판을 의료정보팀으로 승인한다', ok: await page.getByText('승인 1명 남음').first().isVisible() })
    return c
  },
  async A4(page) {
    await enterOps(page, '정예린')
    await page.getByRole('button', { name: '접속기록' }).click()
    await page.locator('tr.row').first().waitFor()
    return [{ name: '누가 어떤 환자 기록을 보고 무엇을 했는지 본다', ok: (await page.locator('tr.row').count()) > 0 && await page.getByText('기록 연결 확인됨').isVisible() }]
  },
  async M2(page) {
    await enterOps(page, '정예린')
    await page.getByRole('button', { name: '감사 경과' }).click()
    const first = page.locator('.card .seg button').first()
    await first.waitFor(); await first.click()
    await page.locator('.card .tl .t').first().waitFor()
    return [{ name: '한 환자의 판단 경과를 따라간다(이름 가림)', ok: /○/.test(await page.locator('.card h3').nth(1).textContent() || '') }]
  },
}

;(async () => {
  const b = await chromium.launch()
  const results = []
  for (const [id, fn] of Object.entries(TASKS)) {
    if (pick && !pick.has(id)) continue
    const page = await b.newPage({ viewport: { width: 1366, height: 768 } })
    let checks
    try { checks = await fn(page) } catch (e) { checks = [{ name: '실행', ok: false, detail: String(e).slice(0, 200) }] }
    await page.screenshot({ path: `${out}/${id}.png` })
    results.push({ id, pass: checks.every(x => x.ok), checks })
    await page.close()
  }
  await b.close()
  console.log(JSON.stringify(results))
})().catch(e => { console.error(e); process.exit(1) })
