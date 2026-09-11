const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const page = readFileSync(`${__dirname}/../src/video_report_agent/static/index.html`, 'utf8');
const source = page.slice(page.indexOf('      async function showHistory()'),
  page.indexOf('      async function generateReport()'));

function element() {
  return { children: [], append(...items) { this.children.push(...items); } };
}

async function history(publicMode, runs, reports, failedEndpoint) {
  const calls = [];
  const historyList = {
    innerHTML: '', children: [],
    replaceChildren(...items) { this.children = items; }
  };
  const context = vm.createContext({
    publicMode, historyList, labels: {},
    historyDialog: { showModal() {} },
    document: { createElement: element },
    fetch: async endpoint => {
      calls.push(endpoint);
      return { ok: endpoint !== failedEndpoint,
        json: async () => endpoint.endsWith('/runs') ? { runs } : { reports } };
    }
  });
  await vm.runInContext(`${source}\nshowHistory()`, context);
  return { calls, historyList };
}

const shared = { run_id: 'shared', state: 'RENDERED', report_url: '/reports/shared/report.html', queue_seq: 1 };

test('Public history shows another owner report with its working link', async () => {
  const { historyList } = await history(true, [], [shared]);
  assert.equal(historyList.children.length, 1);
  assert.equal(historyList.children[0].children[1].children[0].href, shared.report_url);
});

test('Public history keeps own pending and failed tasks and deduplicates completed reports', async () => {
  const own = { run_id: 'own', state: 'RENDERED', queue_seq: 2 };
  const { historyList } = await history(true, [own,
    { run_id: 'pending', state: 'QUEUED', queue_seq: 3 },
    { run_id: 'failed', state: 'FAILED', queue_seq: 4, error: 'test failure' }
  ], [shared, own]);
  assert.deepEqual(historyList.children.map(item => item.children[0].children[0].textContent),
    ['failed', 'pending', 'own', 'shared']);
  assert.equal(historyList.children[0].children[1].textContent, 'test failure');
});

test('Local history only requests own runs', async () => {
  const { calls, historyList } = await history(false, [], [shared]);
  assert.deepEqual(calls, ['/api/visual-report/runs']);
  assert.match(historyList.innerHTML, /还没有提交任务/);
});

test('A failed history request displays the error state', async () => {
  const { historyList } = await history(true, [], [], '/api/visual-report/reports');
  assert.match(historyList.innerHTML, /历史报告暂时无法读取/);
});
