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
    return c
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
