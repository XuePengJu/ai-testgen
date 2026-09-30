const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8000';

async function main() {
  // 1. 通过 API 拿 admin token
  const loginRes = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'username=admin&password=Admin@123',
  });
  const loginJson = await loginRes.json();
  const token = loginJson.access_token;
  console.log('[1] login token:', token ? 'OK' : 'FAIL');

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push('[console] ' + m.text()); });
  page.on('pageerror', e => errors.push('[pageerror] ' + e.message));

  // 2. 注入 token 后加载
  await page.addInitScript((t) => {
    localStorage.setItem('aitf_token', t);
  }, token);
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });

  // 3. 等待会话侧栏渲染
  await page.waitForSelector('#chatHistory .hist-item', { timeout: 10000 });
  const histCount = await page.locator('#chatHistory .hist-item').count();
  console.log('[3] conversations rendered:', histCount);
  const hdelCount = await page.locator('#chatHistory .hist-item .h-del').count();
  console.log('[3] hover-delete buttons:', hdelCount);

  // 4. 点击第一个会话（「删除验证」，含关联任务 b90841ccc5b6），回放其任务节点卡
  await page.locator('#chatHistory .hist-item').first().click();
  await page.waitForSelector('.steps-card[data-task-id]', { timeout: 10000 });
  const cardCount = await page.locator('.steps-card[data-task-id]').count();
  console.log('[4] task node cards:', cardCount);
  const delBtnCount = await page.locator('.steps-card .task-del').count();
  console.log('[4] task delete buttons:', delBtnCount);

  // 5. 点击节点卡「删除」→ 确认弹窗
  await page.locator('.steps-card .task-del').first().click();
  await page.waitForSelector('#confirmOverlay.open', { timeout: 5000 });
  const title = await page.locator('#confirmTitle').textContent();
  const msg = await page.locator('#confirmMsg').textContent();
  console.log('[5] confirm title:', title);
  console.log('[5] confirm msg:', msg.replace(/\s+/g, ' ').slice(0, 80));

  // 6. 取消 → 弹窗关闭，节点卡仍在
  await page.locator('#confirmOverlay .c-cancel').click();
  await page.waitForTimeout(300);
  const openAfterCancel = await page.locator('#confirmOverlay').evaluate(el => el.classList.contains('open'));
  const cardAfterCancel = await page.locator('.steps-card[data-task-id]').count();
  console.log('[6] after cancel -> overlay open:', openAfterCancel, ', cards:', cardAfterCancel);

  // 7. 再次删除并确认 → 节点卡移除
  await page.locator('.steps-card .task-del').first().click();
  await page.waitForSelector('#confirmOverlay.open', { timeout: 5000 });
  await page.locator('#confirmOk').click();
  await page.waitForTimeout(1500);
  const cardAfterConfirm = await page.locator('.steps-card[data-task-id]').count();
  console.log('[7] after confirm -> cards:', cardAfterConfirm);

  // 8. 测试会话删除：hover 第一个会话 → 点删除 → 确认
  const beforeConvs = await page.locator('#chatHistory .hist-item').count();
  await page.locator('#chatHistory .hist-item').first().hover();
  await page.locator('#chatHistory .hist-item .h-del').first().click();
  await page.waitForSelector('#confirmOverlay.open', { timeout: 5000 });
  const convTitle = await page.locator('#confirmTitle').textContent();
  console.log('[8] conv confirm title:', convTitle);
  await page.locator('#confirmOk').click();
  await page.waitForTimeout(1500);
  const afterConvs = await page.locator('#chatHistory .hist-item').count();
  console.log('[8] conversations before/after:', beforeConvs, '/', afterConvs);

  console.log('[9] JS errors:', errors.length ? errors.slice(0, 5) : 'none');

  await browser.close();
}

main().catch(e => { console.error('SCRIPT FAIL:', e); process.exit(1); });
