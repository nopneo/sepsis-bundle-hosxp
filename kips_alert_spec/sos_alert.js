/**
 * เตือนสงสัย Sepsis หลังบันทึกสัญญาณชีพผู้ป่วยใน — โรงพยาบาลวังน้ำเขียว
 *
 * เกณฑ์: SOS Score > 3 ร่วมกับ ไข้ (T >= 38) หรือ ตัวเย็น (20 < T < 36) · ผู้ป่วยอายุ >= 15 ปี
 * สูตร SOS ตรงกับหน้าต่างเตือนใน HOSxP (SYS_VN_PRE_SCRIPT) ที่ ER ใช้อยู่ ทุกช่วงและทุกลำดับเงื่อนไข
 * ตรวจแล้วกับ test_vectors.json 54 กรณี (เทียบกับ SQL จริงบน MariaDB)
 *
 * ใช้ได้ทั้งในเบราว์เซอร์ (window.WnkSosAlert) และ Node.js (require)
 * ไม่มี dependency
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.WnkSosAlert = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DEFAULTS = { sosGreaterThan: 3, minAgeYears: 15 };

  /** ค่าว่าง / ไม่ใช่ตัวเลข / <= 0 ถือว่าไม่ได้กรอก -> null */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var x = Number(v);
    return isFinite(x) && x > 0 ? x : null;
  }

  function between(x, lo, hi) {
    return x >= lo && x <= hi;
  }

  // ลำดับ if ต้องคงตามนี้ — ช่วงของสูตรเดิมซ้อนกันบางจุด (ชีพจร 100, RR 35) และเว้นช่องว่างบางจุด
  // (T 36.01-36.09, 38.41-38.49 ได้ 0) ให้ผลตรงกับหน้าต่างเตือน ER ทุกค่า
  function scoreTemp(t) {
    if (t === null || t <= 20) return 0;            // ไม่ได้กรอก / ค่าผิด
    if (between(t, 36.1, 38)) return 0;
    if (between(t, 35.1, 36)) return 1;
    if (between(t, 38.1, 38.4)) return 1;
    if (t <= 35 || t >= 38.5) return 2;
    return 0;
  }

  function scoreSbp(s) {
    if (s === null || s <= 50) return 0;            // ไม่ได้กรอก / ค่าผิด
    if (between(s, 101, 180)) return 0;
    if (between(s, 91, 100)) return 1;
    if (between(s, 181, 199)) return 1;
    if (between(s, 81, 90)) return 2;
    if (s >= 200) return 2;
    if (s <= 80) return 3;
    return 0;
  }

  function scorePulse(p) {
    if (p === null || p <= 20) return 0;            // ไม่ได้กรอก / ค่าผิด
    if (between(p, 51, 100)) return 0;
    if (between(p, 41, 50)) return 1;
    if (between(p, 100, 120)) return 1;
    if (between(p, 121, 139)) return 2;
    if (p <= 40) return 3;
    if (p >= 140) return 3;
    return 0;
  }

  function scoreRr(r) {
    if (r === null || r <= 2) return 0;             // ไม่ได้กรอก / ค่าผิด
    if (between(r, 9, 20)) return 0;
    if (between(r, 21, 25)) return 1;
    if (between(r, 26, 35)) return 2;
    if (r >= 35 || r <= 8) return 3;
    return 0;
  }

  /** ไข้ >= 38 หรือตัวเย็น < 36 (ค่า <= 20 = กรอกผิด ไม่นับ) */
  function feverOrHypothermia(temp) {
    var t = num(temp);
    return t !== null && (t >= 38 || (t > 20 && t < 36));
  }

  /**
   * คำนวณ SOS และตัดสินว่าต้องเตือนหรือไม่
   * @param {{temp, sbp, pulse, rr, age_y}} v ค่าที่พยาบาลเพิ่งบันทึก (string หรือ number ก็ได้)
   * @param {{sosGreaterThan?: number, minAgeYears?: number}} [opts] ค่าจาก kips_sys_var
   * @returns {{parts: {temp, sbp, pulse, rr}, sos: number, feverOrHypothermia: boolean, alert: boolean}}
   */
  function evaluate(v, opts) {
    var o = Object.assign({}, DEFAULTS, opts || {});
    var parts = {
      temp: scoreTemp(num(v.temp)),
      sbp: scoreSbp(num(v.sbp)),
      pulse: scorePulse(num(v.pulse)),
      rr: scoreRr(num(v.rr)),
    };
    var sos = parts.temp + parts.sbp + parts.pulse + parts.rr;
    var fever = feverOrHypothermia(v.temp);
    var age = num(v.age_y);
    var adult = age !== null && age >= o.minAgeYears;
    return { parts: parts, sos: sos, feverOrHypothermia: fever, alert: adult && fever && sos > o.sosGreaterThan };
  }

  /** ข้อความในหน้าต่างเตือน (ไม่มีชื่อผู้ป่วย — หน้าจอ KIPS แสดงชื่ออยู่แล้ว) */
  function alertMessage(v, result) {
    var t = num(v.temp);
    var label = { temp: 'T', sbp: 'BP', pulse: 'HR', rr: 'RR' };
    var detail = Object.keys(result.parts)
      .filter(function (k) { return result.parts[k] > 0; })
      .map(function (k) { return label[k] + ' ' + result.parts[k]; })
      .join(' · ');
    return {
      title: 'เตือนสงสัย SEPSIS (หอผู้ป่วย)',
      lines: [
        'SOS Score = ' + result.sos + (detail ? ' (' + detail + ')' : ''),
        (t !== null && t >= 38 ? 'มีไข้ T ' : 'ตัวเย็น T ') + t + ' °C',
        'BP ' + (v.sbp || '-') + '/' + (v.dbp || '-') + '  HR ' + (v.pulse || '-') + '  RR ' + (v.rr || '-'),
      ],
      todo: [
        '1. แจ้งแพทย์ประเมินทันที',
        '2. ประเมินแหล่งติดเชื้อ · สงสัย Sepsis → เริ่ม Sepsis Six ภายใน 1 ชม.',
        '3. วัดสัญญาณชีพซ้ำถี่ขึ้น · ติดตาม SpO2 / Urine Output',
      ],
    };
  }

  return {
    DEFAULTS: DEFAULTS,
    evaluate: evaluate,
    feverOrHypothermia: feverOrHypothermia,
    alertMessage: alertMessage,
    _score: { temp: scoreTemp, sbp: scoreSbp, pulse: scorePulse, rr: scoreRr },
  };
});
