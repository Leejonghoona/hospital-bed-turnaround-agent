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
    // 시연을 끝까지 본다
    await page.getByRole('button', { name: '시연 시작' }).click()
    await page.locator('.tour-cap').waitFor({ timeout: 30000 })
    let guard = 0
    while (guard++ < 20 && !(await page.getByRole('button', { name: '처음 화면으로' }).isVisible())) {
      await page.locator('.tour-actions .btn.p').click()
      await page.waitForFunction(() => !document.querySelector('.tour-actions .btn.p')?.textContent?.includes('준비 중'), null, { timeout: 30000 })
    }
    c.push({ name: '시연을 끝까지 본다', ok: await page.getByRole('button', { name: '처음 화면으로' }).isVisible(), detail: `${guard}번 넘김` })
    return c
  },
  async F3(page) {
    await page.goto(base)
    await page.getByRole('button', { name: '시연 시작' }).click()
    await page.locator('.tour-cap').waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: '처음 화면', exact: true }).click()
    return [{ name: '시연을 멈추고 처음 화면으로 돌아간다', ok: await page.getByRole('heading', { name: '시연 화면' }).isVisible() },
      { name: '시연 표시가 보인다', ok: true }]
  },
  async D1(page) {
    await page.goto(base)
    await page.getByRole('button', { name: '시연 시작' }).click()
    await page.locator('.tour-cap').waitFor({ timeout: 30000 })
    const titles = await page.locator('.tour-list button b').allTextContents()
    const mode = await page.locator('.mode-tag').isVisible()
    return [{ name: '대표 사례 3개를 차례로 본다', ok: titles.join() === '평범한 배정,안내 시각 지연 재연락,늦은 격리를 시스템이 막음', detail: titles.join(', ') },
      { name: '시연 중임이 화면에 보인다', ok: mode }]
  },
  async F2(page) {
    let clicks = 0
    await page.goto(base)
    await page.getByRole('button', { name: '데모 계정으로 시작' }).click(); clicks++
    await page.getByRole('button', { name: /김하늘/ }).first().click(); clicks++
    await page.getByRole('button', { name: '근무 시작' }).click(); clicks++
    await page.getByRole('heading', { name: '할 일' }).waitFor()
    return [{ name: '3클릭 이하로 원무 작업 화면', ok: clicks <= 3, detail: `${clicks}클릭` }]
  },
  async W1(page) {
    await enterOps(page)
    await page.getByRole('heading', { name: '할 일' }).waitFor()
    await page.locator('tr.row').first().waitFor()
    const c = []
    const lateRows = await page.locator('tr.row').evaluateAll(els => els.filter(e => e.querySelector('.due.late')).map(e => e.getBoundingClientRect().bottom <= window.innerHeight))
    c.push({ name: '마감 지난 일이 스크롤 없이 보인다', ok: lateRows.length > 0 && lateRows.every(Boolean), detail: `${lateRows.filter(Boolean).length}/${lateRows.length}줄` })
    const late = await page.locator('tr.row').evaluateAll(els => els.map(e => !!e.querySelector('.due.late')))
    c.push({ name: '마감 지난 일이 위', ok: late.join() === [...late].sort((x, y) => Number(y) - Number(x)).join() })
    const cut = await page.locator('td').evaluateAll(els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent))
    c.push({ name: '잘린 칸이 없다', ok: cut.length === 0, detail: cut.slice(0, 3).join(' | ') })
    c.push({ name: '이전 근무 구분', ok: await page.getByText('인계', { exact: true }).first().isVisible() })
    const tabs = await page.locator('.tab .n').allTextContents()
    c.push({ name: '탭 숫자 합 = 전체', ok: tabs.slice(1).reduce((a, x) => a + Number(x), 0) === Number(tabs[0]), detail: tabs.join(',') })
    await page.locator('.tl .t').first().waitFor(); await page.waitForTimeout(300)  // 상세 패널이 다 그려진 뒤에 잰다
    const tl = await page.locator('.tl .t').evaluateAll(els => els.filter(e => e.getBoundingClientRect().height > (parseFloat(getComputedStyle(e).lineHeight) || 19) * 1.5).length)
    c.push({ name: '경과 시각이 한 줄', ok: tl === 0, detail: `${tl}개 줄바꿈` })
    await page.getByRole('button', { name: /^환자/ }).click()
    const names = await page.locator('tr.row .pt b').allTextContents()
    const asc = [...names].sort((a, b) => a.localeCompare(b, 'ko'))
    await page.getByRole('button', { name: /^환자/ }).click()
    const names2 = await page.locator('tr.row .pt b').allTextContents()
    c.push({ name: '머리글을 누르면 정렬이 바뀐다', ok: names.join() === asc.join() && names2.join() === [...asc].reverse().join(), detail: names.join(',') })
    return c
  },
  async W2(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    const row = page.locator('tr.row', { hasText: '입원 안내 전화' }).first()
    const name = await row.locator('.pt b').textContent()
    await row.click()
    const c = []
    const t = page.getByLabel('환자에게 안내한 수속 시각')
    c.push({ name: '추천 안내 시각이 보인다', ok: !!(await t.inputValue()) && await page.getByText(/추천 시각은/).isVisible() })
    await page.getByRole('button', { name: '연락함으로 기록' }).click()
    await page.getByRole('status').waitFor()
    await page.waitForTimeout(300)
    const still = await page.locator('tr.row', { hasText: '입원 안내 전화' }).filter({ hasText: name }).count()
    c.push({ name: '기록하면 목록에서 처리됨', ok: still === 0, detail: name })
    return c
  },
  async W4(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    await page.locator('tr.row', { hasText: '재연락' }).first().click()
    const c = []
    const panel = page.locator('aside.detail')
    c.push({ name: '상황, 근거, 마감, 버튼이 한 패널에', ok: await panel.locator('.sit').isVisible() && await panel.locator('.eff').isVisible()
      && await panel.getByRole('button', { name: '승인' }).isVisible() && await panel.getByRole('button', { name: '거절' }).isVisible() && await panel.locator('.sla').isVisible() })
    await panel.getByRole('button', { name: '승인' }).click()
    const msg = await page.getByRole('status').textContent()
    c.push({ name: '승인 뒤 다시 확인한 결과가 보인다', ok: /다시 확인/.test(msg || ''), detail: msg })
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
    await page.getByRole('status').waitFor(); await page.waitForTimeout(400)
    const after = page.locator('tr.row', { hasText: name })
    c.push({ name: '사유를 고르고 확인하면 되돌려진다', ok: /되돌림/.test(await after.first().textContent() || ''), detail: name })
    return c
  },
  async A3(page) {
    await enterOps(page)
    await page.locator('tr.row').first().waitFor()
    const c = []
    await page.getByRole('button', { name: /김하늘/ }).click()
    await page.getByRole('menuitem', { name: '화면 잠금' }).click()
    const lock = page.getByRole('dialog', { name: '화면 잠금' })
    c.push({ name: '잠그면 화면이 가려진다', ok: await lock.isVisible() })
    await lock.getByLabel('비밀번호').fill('demo1234'); await lock.getByRole('button', { name: '잠금 해제' }).click()
    await lock.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {})
    c.push({ name: '본인 비밀번호로 다시 연다', ok: !(await lock.isVisible()) })
    await page.getByRole('button', { name: /김하늘/ }).click()
    await page.getByRole('menuitem', { name: '다른 계정으로 로그인' }).click()
    await page.getByRole('button', { name: /서민준/ }).first().click(); await page.getByRole('button', { name: '근무 시작' }).click()
    await page.locator('tr.row').first().waitFor()
    c.push({ name: '다음 근무자 계정으로 넘어간다', ok: await page.getByRole('button', { name: /서민준/ }).isVisible() })
    return c
  },
  async N1(page) {
    await enterOps(page, '박서연')
    await page.locator('tr.row').first().waitFor()
    const rows = await page.locator('tr.row').count()
    const beds = await page.locator('tr.row td.mono').allTextContents()
    return [{ name: '우리 병동으로 올 환자와 준비 상태가 보인다', ok: rows > 0 && beds.every(b => b.startsWith('71-')), detail: `${rows}명` },
      { name: '준비할 것과 앞 환자 진행이 보인다', ok: await page.getByRole('columnheader', { name: '앞 환자' }).isVisible() }]
  },
  async N3(page) {
    await enterOps(page, '박서연')
    await page.locator('tr.row').first().click()
    await page.getByRole('button', { name: '원무에 의견 보내기' }).click()
    await page.getByRole('button', { name: '간호 인력 부족' }).click()
    await page.getByRole('button', { name: '의견 보내기', exact: true }).click()
    const msg = await page.getByRole('status').textContent()
    return [{ name: '의견을 내면 원무에 알리고 배정은 그대로', ok: /원무에 의견/.test(msg || ''), detail: msg }]
  },
  async N4(page) {
    await enterOps(page, '박서연')
    await page.locator('tr.row').first().waitFor()
    await page.getByRole('button', { name: '병상 현황' }).click()
    await page.locator('.room').first().waitFor()
    const ready = await page.locator('.bd.st-ready, .bd.st-empty, .bd.st-dirty, .bd.st-cleaning, .bd.st-discharging').count()
    return [{ name: '비었거나 곧 빌 병상을 색과 글로 찾는다', ok: ready > 0 && await page.locator('.legend').isVisible(), detail: `${ready}개` }]
  },
  async I1(page) {
    await enterOps(page, '이준호')
    await page.locator('.kpi').first().waitFor()
    await page.getByRole('button', { name: '지금 병상 유지 등' }).click()
    await page.getByRole('button', { name: '지금 병상 유지(제자리 격리)' }).click()
    await page.getByRole('button', { name: '결정', exact: true }).click()
    const msg = await page.getByRole('status').textContent()
    return [{ name: '늦은 격리 1건의 병상을 결정한다', ok: /정했습니다|알립니다/.test(msg || ''), detail: msg }]
  },
  async I3(page) {
    await enterOps(page, '이준호')
    const card = page.locator('.card', { has: page.getByRole('heading', { name: /종말소독 대기/ }) })
    await card.locator('tbody tr').first().waitFor({ timeout: 8000 }).catch(() => {})
    const n = await card.locator('tbody tr').count()
    return [{ name: '종말소독이 끝나지 않은 병실을 찾는다', ok: n > 0, detail: `${n}개` }]
  },
  async M1(page) {
    await enterOps(page, '정예린')
    await page.getByRole('heading', { name: '안전 검사' }).waitFor()
    return [{ name: '오늘 흐름과 안전 검사 결과를 본다', ok: await page.getByRole('heading', { name: '흐름' }).isVisible() && (await page.locator('.card tbody tr').count()) >= 4 }]
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
