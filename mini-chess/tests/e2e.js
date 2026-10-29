// Прогін у браузері: node mini-chess/tests/e2e.js
// Потрібен Playwright з Chromium. Сторінка віддається з index.html через перехоплення запитів
// (адреса http://mini-chess.test/), а Ably підмінено локальною заглушкою на BroadcastChannel,
// тож тест не ходить в інтернет і не потребує ключа.
'use strict';

var fs = require('fs');
var path = require('path');
var assert = require('assert/strict');
var playwright = require('playwright');

var ORIGIN = 'http://mini-chess.test';
var HTML = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
var HTML_ONLINE = HTML.replace('var ABLY_KEY = "<КЛЮЧ_ABLY>";', 'var ABLY_KEY = "test.key:for-e2e-only";');
assert.notEqual(HTML, HTML_ONLINE, 'рядок ABLY_KEY має бути в index.html');

// Заглушка Ably з тим самим API, що використовує сторінка.
function fakeAbly() {
  window.Ably = {
    Realtime: function () {
      this.connection = { on: function (fn) { setTimeout(function () { fn({ current: 'connected' }); }, 10); } };
      this.channels = {
        get: function (name) {
          var bc = new BroadcastChannel(name), subs = [];
          bc.onmessage = function (e) {
            for (var i = 0; i < subs.length; i++) subs[i]({ name: e.data.name, data: e.data.data });
          };
          return {
            subscribe: function (fn) { subs.push(fn); return Promise.resolve(); },
            publish: function (n, d) { bc.postMessage({ name: n, data: JSON.parse(JSON.stringify(d)) }); return Promise.resolve(); }
          };
        }
      };
      this.close = function () {};
    }
  };
}

function log(msg) { process.stdout.write(msg + '\n'); }

async function newPage(ctx, html, problems) {
  var page = await ctx.newPage();
  page.on('console', function (m) { if (m.type() === 'error') problems.push('console: ' + m.text()); });
  page.on('pageerror', function (e) { problems.push('pageerror: ' + e.message); });
  page.on('request', function (r) {
    if (r.url().indexOf(ORIGIN) !== 0 && r.url().indexOf('data:') !== 0) problems.push('зовнішній запит: ' + r.url());
  });
  await page.route(ORIGIN + '/**', function (route) {
    route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
  });
  await page.goto(ORIGIN + '/');
  return page;
}

async function noHScroll(page, where) {
  var r = await page.evaluate(function () {
    return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth };
  });
  assert.ok(r.sw <= r.cw, where + ': горизонтальний скрол ' + r.sw + ' > ' + r.cw);
}

// Ходи гравця вибирає «Лисичка»: читаємо дошку з aria-label клітинок.
async function pickMove(page, level, real, color) {
  return page.evaluate(function (a) {
    var names = { 'пішак': 'p', 'тура': 'r', 'слон': 'b', 'кінь': 'n', 'ферзь': 'q', 'король': 'k' };
    var s = window.MiniChess.createGame(a.level, a.real, '5/5/5/5/5', a.color);
    var sqs = document.querySelectorAll('#board .sq');
    for (var i = 0; i < 25; i++) {
      var m = /, (білі|чорні): (\S+)$/.exec(sqs[i].getAttribute('aria-label'));
      s.board[i] = m ? { c: m[1] === 'білі' ? 'w' : 'b', t: names[m[2]], id: i + 1 } : null;
    }
    // Стартову кількість і втечі беремо зі справжньої гри та з підпису «Утекли: N».
    s.start = window.MiniChess.createGame(a.level, a.real).start;
    var esc = /Утекли: (\d+)/.exec(document.getElementById('tray-top').textContent);
    s.escaped = { w: 0, b: esc ? parseInt(esc[1], 10) : 0 };
    var mv = window.MiniChess.botFox(s);
    return mv ? [mv.from, mv.to] : null;
  }, { level: level, real: real, color: color });
}

async function tapSq(page, sq) {
  await page.locator('#board .sq').nth(sq).click();
}

async function resultVisible(page) {
  return page.locator('#result').isVisible();
}

// Грає за білих, доки не з'явиться результат. Повертає заголовок результату.
async function playToEnd(page, level, real) {
  for (var turn = 0; turn < 120; turn++) {
    await page.waitForFunction(function () {
      return !document.getElementById('result').classList.contains('hidden') ||
        document.getElementById('status-txt').textContent === 'Твій хід';
    }, null, { timeout: 10000 });
    if (await resultVisible(page)) return page.locator('#result-title').textContent();
    var mv = await pickMove(page, level, real, 'w');
    assert.ok(mv, 'має бути хід');
    await tapSq(page, mv[0]);
    await tapSq(page, mv[1]);
    await page.waitForTimeout(50);
  }
  throw new Error('гра не закінчилась');
}

async function progress(page) {
  return page.evaluate(function () { return JSON.parse(localStorage.getItem('mini-chess-progress-v1') || 'null'); });
}

async function main() {
  var browser = await playwright.chromium.launch();
  var problems = [];
  var device = { viewport: { width: 375, height: 740 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
  var ctx = await browser.newContext(device);
  var page = await newPage(ctx, HTML, problems);

  log('1. Карта рівнів');
  await noHScroll(page, 'карта');
  assert.equal(await page.locator('.tile').count(), 7);
  assert.equal(await page.locator('.tile.locked').count(), 6);
  await page.locator('.tile').nth(3).click({ force: true });
  assert.ok(await page.locator('#scr-map').isVisible(), 'закритий рівень не відкривається');

  log('2. Підказка рівня 1');
  await page.locator('.tile').nth(0).click({ force: true });
  assert.ok(await page.locator('#scr-intro').isVisible());
  assert.match(await page.locator('#hint-text').textContent(), /Пішак ходить/);
  assert.ok(await page.locator('#hint-board .pc-wrap').count() >= 1);
  await noHScroll(page, 'підказка');

  log('3. Рівень 1 проти Ведмедика: тап показує крапки, гра до перемоги');
  await page.click('#btn-bear');
  var box = await page.locator('#board .sq').nth(0).boundingBox();
  assert.ok(box.width >= 40, 'клітинка ' + box.width + ' px');
  await tapSq(page, 7);   // c2
  assert.equal(await page.locator('#board .sq.mv').count(), 1, 'у пішака c2 один хід');
  await tapSq(page, 22);  // c5 (порожня) — зняти вибір
  await tapSq(page, 17);  // c4 — пішак суперника: крапки «не для гри»
  assert.equal(await page.locator('#board .sq.foe').count(), 1);
  await tapSq(page, 17);
  await noHScroll(page, 'гра');
  var title = await playToEnd(page, 1, false);
  assert.equal(title, 'Перемога!');
  var pr = await progress(page);
  assert.ok(pr.stars[0] >= 1 && pr.stars[0] <= 2, 'зірочки за Ведмедика: ' + pr.stars[0]);
  assert.ok(await page.locator('#result-stars .star-on').count() === pr.stars[0]);

  log('4. «Далі» → рівень 2, гра проти Лисички до кінця');
  await page.locator('#result-actions button', { hasText: 'Далі' }).click();
  assert.match(await page.locator('#intro-title').textContent(), /Рівень 2/);
  await page.click('#btn-fox');
  assert.match(await page.locator('#goal-line').textContent(), /утече/);
  title = await playToEnd(page, 2, false);
  assert.ok(['Перемога!', 'Нічия', 'Спробуй ще раз!'].indexOf(title) >= 0, title);
  log('   рівень 2 проти Лисички: ' + title);
  await page.locator('#result-actions button', { hasText: 'Додому' }).click();
  assert.ok(await page.locator('#scr-map').isVisible());

  log('5. Замок вчителя: неправильний і правильний пароль, відкрити всі рівні');
  await page.click('#btn-lock');
  for (var i = 0; i < 4; i++) await page.locator('#pin-pad button', { hasText: '9' }).click();
  await page.waitForTimeout(500);
  assert.ok(await page.locator('#pin').isVisible(), 'з паролем 9999 не пускає');
  var pin = '1234';
  for (i = 0; i < 4; i++) await page.locator('#pin-pad button', { hasText: pin.charAt(i) }).first().click();
  await page.waitForSelector('#scr-teacher:not(.hidden)');
  await page.click('#btn-unlock-all');
  assert.equal((await progress(page)).all, true);
  await noHScroll(page, 'панель вчителя');
  log('   кімната без ключа Ably показує пояснення');
  await page.click('#btn-create-room');
  assert.match(await page.locator('#teacher-msg').textContent(), /ключ Ably/);
  await page.click('#btn-home');
  assert.equal(await page.locator('.tile.locked').count(), 0);

  log('6. Рівень 7, справжні правила, кілька ходів');
  await page.locator('.tile').nth(6).click({ force: true });
  assert.ok(await page.locator('#rules-toggle').isVisible());
  assert.equal(await page.locator('#rules-toggle [data-real="0"]').getAttribute('aria-checked'), 'true', 'за замовчуванням — для малят');
  await page.click('#rules-toggle [data-real="1"]');
  assert.match(await page.locator('#hint-text').textContent(), /Мат/);
  await page.click('#btn-bear');
  assert.match(await page.locator('#goal-line').textContent(), /мат/);
  for (i = 0; i < 3; i++) {
    await page.waitForFunction(function () { return document.getElementById('status-txt').textContent === 'Твій хід'; });
    var mv = await pickMove(page, 7, true, 'w');
    await tapSq(page, mv[0]);
    await tapSq(page, mv[1]);
  }
  await page.click('#btn-home');

  log('7. Звук вимикається й запам’ятовується');
  await page.click('#btn-sound');
  assert.equal((await progress(page)).mute, true);
  await page.reload();
  assert.equal(await page.locator('#btn-sound').getAttribute('aria-pressed'), 'true');

  log('8. Сховища: лише прогрес у localStorage');
  var st = await page.evaluate(function () {
    return { ls: Object.keys(localStorage), ss: sessionStorage.length, ck: document.cookie };
  });
  assert.deepEqual(st.ls, ['mini-chess-progress-v1']);
  assert.equal(st.ss, 0);
  assert.equal(st.ck, '');
  var cookies = await ctx.cookies();
  assert.equal(cookies.length, 0);
  await ctx.close();

  log('9. Онлайн: вчитель створює кімнату (рівень 2), учень заходить за кодом');
  var octx = await browser.newContext(device);
  await octx.addInitScript(fakeAbly);
  var teacher = await newPage(octx, HTML_ONLINE, problems);
  var student = await newPage(octx, HTML_ONLINE, problems);
  await teacher.click('#btn-lock');
  for (i = 0; i < 4; i++) await teacher.locator('#pin-pad button', { hasText: pin.charAt(i) }).first().click();
  await teacher.waitForSelector('#scr-teacher:not(.hidden)');
  await teacher.locator('#teacher-levels button').nth(1).click();
  await teacher.click('#btn-create-room');
  await teacher.waitForSelector('#scr-game:not(.hidden)');
  var code = await teacher.locator('#room-code').textContent();
  assert.match(code, /^\d{4}$/);
  var a1 = await teacher.locator('#board .sq').nth(0).evaluate(function (n) { return n.style.left + ' ' + n.style.top; });
  assert.equal(a1, '80% 0%', 'у вчителя дошка перевернута (a1 справа вгорі)');

  await student.click('#btn-join');
  for (i = 0; i < 4; i++) await student.locator('#join-pad button', { hasText: code.charAt(i) }).first().click();
  await student.waitForSelector('#scr-game:not(.hidden)');
  assert.match(await student.locator('#top-title').textContent(), /Рівень 2/);
  assert.equal(await student.locator('#status-txt').textContent(), 'Твій хід');
  await teacher.waitForFunction(function () { return /Учень у грі/.test(document.getElementById('net-status').textContent); });

  log('   учень ходить турою c1→c3, вчитель бачить хід');
  await tapSq(student, 2);
  await tapSq(student, 12);
  await teacher.waitForFunction(function () {
    return /білі: тура/.test(document.querySelectorAll('#board .sq')[12].getAttribute('aria-label'));
  });
  await teacher.waitForFunction(function () { return document.getElementById('status-txt').textContent === 'Твій хід'; });
  log('   вчитель ходить пішаком a4→a3, учень бачить хід');
  await tapSq(teacher, 15);
  await tapSq(teacher, 10);
  await student.waitForFunction(function () {
    return /чорні: пішак/.test(document.querySelectorAll('#board .sq')[10].getAttribute('aria-label'));
  });
  await student.waitForFunction(function () { return document.getElementById('status-txt').textContent === 'Твій хід'; });

  log('   учень не може ходити за вчителя');
  await tapSq(student, 18);
  await tapSq(student, 13);
  await student.waitForTimeout(300);
  assert.match(await student.locator('#board .sq').nth(18).getAttribute('aria-label'), /чорні: пішак/);

  log('   «Нова гра» у вчителя скидає партію в учня');
  await teacher.click('#btn-restart');
  await student.waitForFunction(function () {
    return /білі: тура/.test(document.querySelectorAll('#board .sq')[2].getAttribute('aria-label'));
  });
  await noHScroll(student, 'онлайн учень');
  await noHScroll(teacher, 'онлайн вчитель');
  await octx.close();
  await browser.close();

  if (problems.length) {
    log('ПРОБЛЕМИ:\n' + problems.join('\n'));
    process.exit(1);
  }
  log('OK: усі сценарії пройдено, помилок у консолі й зовнішніх запитів немає.');
}

main().catch(function (e) {
  process.stderr.write((e && e.stack) || String(e));
  process.stderr.write('\n');
  process.exit(1);
});
