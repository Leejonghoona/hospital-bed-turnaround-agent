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
    c.push({ name: '두 카드 크기가 같다', ok: !!a && !!b && a[0] === b[0] && a[1] === b[1], detail: JSON.stringify([a, b]) })
    return c
  },
  async F2(page) {
    let clicks = 0
    await page.goto(base)
    await page.getByRole('button', { name: '데모 계정으로 시작' }).click(); clicks++
    await page.getByRole('button', { name: /김하늘/ }).first().click(); clicks++
    await page.getByRole('button', { name: '근무 시작' }).click(); clicks++
    await page.getByRole('heading', { name: '오늘 할 일' }).waitFor()
    return [{ name: '3클릭 이하로 원무 작업 화면', ok: clicks <= 3, detail: `${clicks}클릭` }]
  },
  async W1(page) {
    await enterOps(page)
    await page.getByRole('heading', { name: '오늘 할 일' }).waitFor()
    await page.locator('tr.row').first().waitFor()
    const c = []
    const rows = await page.locator('tr.row').count()
    const visible = await page.locator('tr.row').evaluateAll(els => els.filter(e => { const r = e.getBoundingClientRect(); return r.bottom <= window.innerHeight }).length)
    c.push({ name: '할 일 전체가 스크롤 없이 보인다', ok: rows > 0 && visible === rows, detail: `${visible}/${rows}줄` })
    const late = await page.locator('tr.row').evaluateAll(els => els.map(e => !!e.querySelector('.due.late')))
    c.push({ name: '마감 지난 일이 위', ok: late.join() === [...late].sort((x, y) => Number(y) - Number(x)).join() })
    const cut = await page.locator('td').evaluateAll(els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent))
    c.push({ name: '잘린 칸이 없다', ok: cut.length === 0, detail: cut.slice(0, 3).join(' | ') })
    c.push({ name: '이전 근무 구분', ok: await page.getByRole('tab', { name: /이전 근무/ }).isVisible() })
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
