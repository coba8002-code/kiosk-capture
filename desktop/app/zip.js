/* 최소 ZIP 작성기 — 라이브러리 없이 번들을 묶는다.
 *
 * 왜 직접 쓰는가
 *   수집 앱은 현장에서 오프라인으로 돌아야 하고, 외부 스크립트를 받아올 수 없다.
 *   그런데 넣을 것은 이미 압축된 JPEG·MP4 라서 다시 압축해봐야 얻는 게 없다.
 *   그래서 무압축(store) ZIP 만 쓴다 — 규격이 단순해 100줄이면 끝난다.
 *
 * 지원: store(무압축), UTF-8 파일명, ZIP64 미지원(4GB 미만이면 충분).
 */
(function (global) {
  'use strict';

  // CRC-32 (IEEE 802.3) — ZIP 규격이 요구한다
  const CRC_TABLE = (function () {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  function dosDateTime(d) {
    const time = ((d.getHours() & 0x1F) << 11) | ((d.getMinutes() & 0x3F) << 5)
               | ((Math.floor(d.getSeconds() / 2)) & 0x1F);
    const date = (((d.getFullYear() - 1980) & 0x7F) << 9) | (((d.getMonth() + 1) & 0x0F) << 5)
               | (d.getDate() & 0x1F);
    return { time, date };
  }

  function w16(a, o, v) { a[o] = v & 0xFF; a[o + 1] = (v >>> 8) & 0xFF; }
  function w32(a, o, v) { w16(a, o, v & 0xFFFF); w16(a, o + 2, (v >>> 16) & 0xFFFF); }

  /**
   * @param {{name:string, data:Uint8Array}[]} files
   * @returns {Blob}
   */
  function createZip(files) {
    const enc = new TextEncoder();
    const now = new Date();
    const { time, date } = dosDateTime(now);

    const parts = [];
    const central = [];
    let offset = 0;

    for (const f of files) {
      const nameBytes = enc.encode(f.name);
      const data = f.data;
      const crc = crc32(data);

      // 로컬 파일 헤더 (30 bytes + name)
      const lfh = new Uint8Array(30 + nameBytes.length);
      w32(lfh, 0, 0x04034B50);
      w16(lfh, 4, 20);            // version needed
      w16(lfh, 6, 0x0800);        // flag: UTF-8 파일명
      w16(lfh, 8, 0);             // method: store
      w16(lfh, 10, time);
      w16(lfh, 12, date);
      w32(lfh, 14, crc);
      w32(lfh, 18, data.length);  // compressed
      w32(lfh, 22, data.length);  // uncompressed
      w16(lfh, 26, nameBytes.length);
      w16(lfh, 28, 0);            // extra len
      lfh.set(nameBytes, 30);

      parts.push(lfh, data);

      // 중앙 디렉터리 항목 (46 bytes + name)
      const cdh = new Uint8Array(46 + nameBytes.length);
      w32(cdh, 0, 0x02014B50);
      w16(cdh, 4, 20);            // version made by
      w16(cdh, 6, 20);            // version needed
      w16(cdh, 8, 0x0800);
      w16(cdh, 10, 0);
      w16(cdh, 12, time);
      w16(cdh, 14, date);
      w32(cdh, 16, crc);
      w32(cdh, 20, data.length);
      w32(cdh, 24, data.length);
      w16(cdh, 28, nameBytes.length);
      w16(cdh, 30, 0);            // extra
      w16(cdh, 32, 0);            // comment
      w16(cdh, 34, 0);            // disk
      w16(cdh, 36, 0);            // internal attr
      w32(cdh, 38, 0);            // external attr
      w32(cdh, 42, offset);
      cdh.set(nameBytes, 46);
      central.push(cdh);

      offset += lfh.length + data.length;
    }

    const centralSize = central.reduce((n, c) => n + c.length, 0);

    // 끝 레코드 (22 bytes)
    const eocd = new Uint8Array(22);
    w32(eocd, 0, 0x06054B50);
    w16(eocd, 4, 0);
    w16(eocd, 6, 0);
    w16(eocd, 8, files.length);
    w16(eocd, 10, files.length);
    w32(eocd, 12, centralSize);
    w32(eocd, 16, offset);
    w16(eocd, 20, 0);

    return new Blob([...parts, ...central, eocd], { type: 'application/zip' });
  }

  global.KfaZip = { createZip, crc32 };
})(window);
