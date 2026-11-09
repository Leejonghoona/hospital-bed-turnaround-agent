// 화면 스크린샷 브라우저 조작 (scripts/screenshots.py가 Streamlit을 띄운 뒤 부른다). 합성 데이터 화면만 찍는다.
const { chromium } = require('playwright');
const [port, out, scale, only] = process.argv.slice(2);
const SCENES = [['day', '1. 평범한 하루', 3], ['exception', '2. 예외와 승인', 4], ['onboard', '3. 새 병원 온보딩과 자율권', 5]];

async function idle(page) {
  await page.waitForTimeout(600);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 180000 });
  await page.waitForTimeout(900);
}

(async () => {
  const b = await chromium.launch();
  const page = await b.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: Number(scale || 2) });
  for (const [sid, title, n] of SCENES) {
    if (only && only !== sid) continue;
    await page.goto(`http://127.0.0.1:${port}`);
    await idle(page);
    const side = page.getByTestId('stSidebar');
    await side.getByTestId('stSelectbox').click();
    await page.getByRole('option', { name: title }).click();
    await idle(page);
    for (let i = 1; i <= n; i++) {
      await page.getByRole('button', { name: '다음 ▶' }).click();
      await idle(page);
      const f = `${out}/scene_${sid}_${i}.png`;
      await page.screenshot({ path: f, fullPage: true });
      const text = await page.innerText('body');
      console.log(JSON.stringify({ file: f, step: i, ok: text.includes('화면') && !text.includes('Traceback') }));
    }
  }
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
