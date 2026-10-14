const assert = require('assert');
const { between, SHARED, FORMATTERS, suite, vm } = require('./harness');

const fn = between('function buildAccuracyTracker', '\n}\n') + '\n}\n';
const ctx = {}; vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + fn, ctx);
const build = d => ctx.buildAccuracyTracker(d, 100);

(async () => {
  const { t, done } = suite('Prediction accuracy tracker');
  await t('No run date means nothing is shown', async () => assert.equal(build({}), ''));
  await t('Pending: says nothing is scored yet and never claims a result', async () => {
    const h = build({ run_date: '2026-10-09', current_price: 100, target_1w: 105, last_outcome: null });
    assert(h.includes('No scored prediction yet') && h.includes('2026-10-09') && !h.includes('Direction Correct') && !h.includes('Direction Wrong'));
  });
  await t('Stale legacy fields are ignored', async () => {
    const h = build({ run_date: '2026-10-09', current_price: 100, target_1w: 105, actual_1w: 90, direction_correct_1w: 0, last_outcome: null });
    assert(!h.includes('Direction Wrong') && h.includes('No scored prediction yet'));
  });
  await t('A scored hit uses the outcome\'s own prediction and price', async () => {
    const h = build({ run_date: '2026-10-09', current_price: 500, target_1w: 520, last_outcome: { run_date: '2026-09-25', price_at_run: 100, target_1w: 105, actual_price: 108, eval_date: '2026-10-06', direction_correct: 1 } });
    assert(h.includes('Direction Correct') && h.includes('2026-09-25') && h.includes('$105.00') && h.includes('$108.00') && h.includes('↑ Up') && h.includes('2.86%'));
  });
  await t('A scored miss is shown as wrong', async () => {
    const h = build({ run_date: 'x', last_outcome: { run_date: '2026-09-25', price_at_run: 100, target_1w: 105, actual_price: 95, eval_date: '2026-10-06', direction_correct: 0 } });
    assert(h.includes('Direction Wrong') && h.includes('↓ Down'));
  });
  await t('Dates are escaped', async () => { const h = build({ run_date: '<img onerror=1>', last_outcome: null }); assert(!h.includes('<img') && h.includes('&lt;img')); });
  done();
})();
