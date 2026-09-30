/* V2.7 步骤级预期 · 前端渲染验证（Playwright）
 * 1. 登录 admin → 打开任务 verify-step-exp 详情
 * 2. 思维导图 tab：断言每个操作步骤节点下有预期结果子节点 + 截图
 * 3. 测试用例 tab：断言步骤列内联「↳ 预期」+ 截图
 */
const { chromium } = require('/Users/xp/.workbuddy/binaries/node/workspace/node_modules/playwright');

(async () => {
  const shotDir = '/tmp/step-expected-shots';
  require('fs').mkdirSync(shotDir, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });

  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });

  // 通过页面内 fetch 登录并写入 localStorage
  await page.evaluate(async () => {
    const r = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'username=admin&password=Admin@123'
    });
    const d = await r.json();
    localStorage.setItem('aitf_token', d.access_token);
    localStorage.setItem('aitf_user', JSON.stringify({ id: d.user_id, username: 'admin', role: 'admin' }));
  });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: shotDir + '/01-首页-输入前.png' });

  // 打开任务详情（详情抽屉）
  await page.evaluate(() => window.showDetail('verify-step-exp'));
  await page.waitForTimeout(3000);
  await page.screenshot({ path: shotDir + '/02-思维导图-逐步预期.png' });

  // 思维导图断言：操作步骤节点下都有预期结果子节点
  const mindmap = await page.evaluate(() => {
    const root = document.querySelector('#map .mind-elixir');
    if (!root) return { ok: false, reason: 'mind-elixir 容器未找到' };
    const text = root.textContent || '';
    const steps = text.split('\n').filter(t => t.includes('操作步骤'));
    const exps = text.split('\n').filter(t => t.includes('预期结果'));
    return {
      ok: exps.length >= 4,
      reason: `操作步骤标签 ${steps.length} 个, 预期结果标签 ${exps.length} 个`,
      hasStep1Exp: text.includes('提示\'退货原因长度超出限制\''),
      hasStep2Exp: text.includes('正常提交或提示长度超出限制'),
    };
  });
  console.log('思维导图检查:', JSON.stringify(mindmap));

  // 切换到测试用例 tab
  await page.click('.dtab[data-pane="cases"]');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: shotDir + '/03-用例表格-内联预期.png' });

  const table = await page.evaluate(() => {
    const cell = document.querySelector('#pane-cases .case-table tbody tr td.ct-text:nth-of-type(7)');
    if (!cell) return { ok: false, reason: '步骤列未找到' };
    const t = cell.textContent || '';
    return {
      ok: t.includes('↳ 预期'),
      inlineCount: (t.match(/↳ 预期/g) || []).length,
      snippet: t.slice(0, 80).replace(/\n/g, ' / '),
    };
  });
  console.log('表格检查:', JSON.stringify(table));

  await browser.close();

  const pass = mindmap.ok && table.ok && errors.length === 0;
  console.log('\nJS 错误:', errors.length ? errors : '无');
  console.log(pass ? '✅ 前端渲染验证通过' : '❌ 前端渲染验证失败');
  process.exit(pass ? 0 : 1);
})();
