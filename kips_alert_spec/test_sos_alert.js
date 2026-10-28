// รันด้วย: node test_sos_alert.js — ตรวจ sos_alert.js กับ test_vectors.json ทุกกรณี
'use strict';
const assert = require('assert');
const path = require('path');
const SosAlert = require('./sos_alert.js');
const vectors = require(path.join(__dirname, 'test_vectors.json'));

let failed = 0;
for (const c of vectors) {
  const r = SosAlert.evaluate(c.input);
  const got = { parts: r.parts, sos: r.sos, fever_or_hypothermia: r.feverOrHypothermia, alert: r.alert };
  try {
    assert.deepStrictEqual(got, c.expect);
  } catch (e) {
    failed++;
    console.log(`FAIL #${c.id} ${c.desc}\n  ได้   ${JSON.stringify(got)}\n  ควรได้ ${JSON.stringify(c.expect)}`);
  }
}

// ปรับเกณฑ์ผ่าน opts ได้
assert.strictEqual(SosAlert.evaluate({ temp: 38.6, sbp: 120, pulse: 95, rr: 22, age_y: 40 }).alert, false);
assert.strictEqual(SosAlert.evaluate({ temp: 38.6, sbp: 120, pulse: 95, rr: 22, age_y: 40 }, { sosGreaterThan: 2 }).alert, true);
// ค่าเป็น string จากช่องกรอกในฟอร์มก็ได้
assert.strictEqual(SosAlert.evaluate({ temp: '39.2', sbp: '82', pulse: '128', rr: '28', age_y: '60' }).sos, 8);
// ข้อความหน้าต่าง
const v = { temp: 39.2, sbp: 82, dbp: 50, pulse: 128, rr: 28, age_y: 60 };
const msg = SosAlert.alertMessage(v, SosAlert.evaluate(v));
assert.ok(msg.lines[0].startsWith('SOS Score = 8'));
assert.ok(msg.lines[1].startsWith('มีไข้'));

console.log(`${vectors.length - failed}/${vectors.length} กรณีผ่าน`);
process.exit(failed ? 1 : 0);
