// Тести логіки міні-шахів 5×5. Запуск: node --test mini-chess/tests/
// Рушій береться прямо з index.html (між ENGINE:START і ENGINE:END), окремої копії немає.
'use strict';

var test = require('node:test');
var assert = require('node:assert/strict');
var fs = require('fs');
var path = require('path');
var vm = require('vm');

var html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
var code = html.slice(html.indexOf('/* ENGINE:START */'), html.indexOf('/* ENGINE:END */'));
var sandbox = {};
vm.runInNewContext(code + '\nthis.MC = MiniChess;', sandbox);
var MC = sandbox.MC;

function sq(name) { return MC.sqIndex(name); }
function game(level, setup, turn, real) { return MC.createGame(level, !!real, setup, turn); }
// Масиви з рушія належать іншому контексту vm, тож переносимо в звичайний масив.
function names(moves, withFrom) {
  var out = [];
  for (var i = 0; i < moves.length; i++) out.push((withFrom ? MC.sqName(moves[i].from) : '') + MC.sqName(moves[i].to));
  return out.sort();
}
function targets(state, from) { return names(MC.movesFrom(state, sq(from)), false); }
function play(state, list) {
  for (var i = 0; i < list.length; i++) {
    var m = MC.findMove(state, sq(list[i].slice(0, 2)), sq(list[i].slice(2, 4)));
    assert.ok(m, 'хід ' + list[i] + ' має бути дозволений');
    state = MC.applyMove(state, m);
  }
  return state;
}
function move(state, uci) { return play(state, [uci]); }
function perft(s, d) {
  if (d === 0) return 1;
  var ms = MC.legalMoves(s), n = 0;
  for (var i = 0; i < ms.length; i++) n += perft(MC.applyMove(s, ms[i]), d - 1);
  return n;
}
function seeded(seed) {
  return function () {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
}

/* ---------- Розстановки ---------- */

test('стартові позиції всіх 7 рівнів', function () {
  var expect = [
    { w: 5, b: 5 }, { w: 1, b: 4 }, { w: 1, b: 4 }, { w: 1, b: 4 }, { w: 1, b: 5 }, { w: 6, b: 6 }, { w: 10, b: 10 }
  ];
  for (var lv = 1; lv <= 7; lv++) {
    var s = MC.createGame(lv);
    assert.equal(MC.countPieces(s.board, 'w'), expect[lv - 1].w, 'рівень ' + lv + ' білі');
    assert.equal(MC.countPieces(s.board, 'b'), expect[lv - 1].b, 'рівень ' + lv + ' чорні');
    assert.equal(s.turn, 'w');
    assert.equal(s.over, false);
    assert.ok(MC.legalMoves(s).length > 0);
  }
  var g7 = MC.createGame(7).board;
  assert.equal(g7[sq('a1')].t, 'r');
  assert.equal(g7[sq('b1')].t, 'n');
  assert.equal(g7[sq('c1')].t, 'b');
  assert.equal(g7[sq('d1')].t, 'q');
  assert.equal(g7[sq('e1')].t, 'k');
  assert.equal(g7[sq('e5')].t, 'k');
  assert.equal(g7[sq('e5')].c, 'b');
  assert.equal(MC.createGame(2).board[sq('c1')].t, 'r');
  assert.equal(MC.createGame(3).board[sq('b2')].t, 'b');
  assert.equal(MC.createGame(4).board[sq('d2')].t, 'n');
  assert.equal(MC.createGame(5).board[sq('c1')].t, 'q');
});

test('id фігур унікальні', function () {
  var b = MC.createGame(7).board, seen = {};
  b.forEach(function (p) {
    if (!p) return;
    assert.ok(!seen[p.id]);
    seen[p.id] = true;
  });
});

/* ---------- Ходи фігур ---------- */

test('пішак: на одну клітинку вперед, без подвійного ходу', function () {
  var s = game(1, '5/5/5/2P2/5');
  assert.deepEqual(targets(s, 'c2'), ['c3']);
  s = game(1, '5/2p2/5/5/5', 'b');
  assert.deepEqual(targets(s, 'c4'), ['c3']);
});

test('пішак: заблокований спереду не ходить, б’є лише навскоси суперника', function () {
  var s = game(1, '5/5/1pp2/2P2/5');
  assert.deepEqual(targets(s, 'c2'), ['b3']);
  s = game(1, '5/5/1PpP1/2P2/5');
  assert.deepEqual(targets(s, 'c2'), []);
  s = game(1, '5/5/5/P4/5');
  assert.deepEqual(targets(s, 'a2'), ['a3']);
});

test('тура, слон, кінь, ферзь, король з центру порожньої дошки', function () {
  assert.deepEqual(targets(game(2, '5/5/2R2/5/5'), 'c3'), ['a3', 'b3', 'c1', 'c2', 'c4', 'c5', 'd3', 'e3']);
  assert.deepEqual(targets(game(3, '5/5/2B2/5/5'), 'c3'), ['a1', 'a5', 'b2', 'b4', 'd2', 'd4', 'e1', 'e5']);
  assert.deepEqual(targets(game(4, '5/5/2N2/5/5'), 'c3'), ['a2', 'a4', 'b1', 'b5', 'd1', 'd5', 'e2', 'e4']);
  assert.equal(targets(game(5, '5/5/2Q2/5/5'), 'c3').length, 16);
  assert.deepEqual(targets(game(6, '5/5/2K2/5/5'), 'c3'), ['b2', 'b3', 'b4', 'c2', 'c4', 'd2', 'd3', 'd4']);
  assert.deepEqual(targets(game(4, '5/5/5/5/N4'), 'a1'), ['b3', 'c2']);
});

test('далекобійні фігури зупиняються перед своєю і на чужій фігурі', function () {
  var s = game(7, '2p2/5/P1R1p/5/5');
  assert.deepEqual(targets(s, 'c3'), ['b3', 'c1', 'c2', 'c4', 'c5', 'd3', 'e3']);
  var m = MC.findMove(s, sq('c3'), sq('e3'));
  assert.ok(m.capture);
  assert.equal(MC.findMove(s, sq('c3'), sq('a3')), null);
});

test('кінь перестрибує через фігури', function () {
  var s = game(7, '5/5/1PPP1/1PNP1/1PPP1');
  assert.deepEqual(targets(s, 'c2'), ['a1', 'a3', 'b4', 'd4', 'e1', 'e3']);
});

test('ходити можна лише своїми фігурами й лише у свою чергу', function () {
  var s = MC.createGame(1);
  assert.equal(MC.findMove(s, sq('a4'), sq('a3')), null);
  s = move(s, 'a2a3');
  assert.equal(s.turn, 'b');
  assert.equal(MC.findMove(s, sq('b2'), sq('b3')), null);
  assert.ok(MC.findMove(s, sq('b4'), sq('a3')));
});

/* ---------- Перетворення пішака ---------- */

test('рівень 7: пішак на останньому ряду стає ферзем (білі й чорні), id зберігається', function () {
  var s = game(7, 'k4/2P2/5/5/K3p', 'w');
  var id = s.board[sq('c4')].id;
  s = move(s, 'c4c5');
  assert.equal(s.board[sq('c5')].t, 'q');
  assert.equal(s.board[sq('c5')].c, 'w');
  assert.equal(s.board[sq('c5')].id, id);
  assert.equal(s.last.promo, true);
  assert.equal(s.over, false);
  s = game(7, 'k4/5/5/3p1/K4', 'b');
  s = move(s, 'd2d1');
  assert.equal(s.board[sq('d1')].t, 'q');
  assert.equal(s.board[sq('d1')].c, 'b');
});

test('рівень 7: перетворення зі взяттям', function () {
  var s = game(7, 'k1r2/3P1/5/5/K4');
  s = move(s, 'd4c5');
  assert.equal(s.board[sq('c5')].t, 'q');
  assert.equal(s.last.capture, true);
});

test('рівень 7, справжні правила: новий ферзь одразу дає мат', function () {
  var s = game(7, '3k1/P4/3K1/5/5', 'w', true);
  s = move(s, 'a4a5');
  assert.equal(s.board[sq('a5')].t, 'q');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'mate');
});

/* ---------- Рівень 1: пішаковий бій ---------- */

test('рівень 1: пішак дійшов до останнього ряду — перемога', function () {
  var s = game(1, '5/1P3/5/5/3p1');
  s = move(s, 'b4b5');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'promote');
  assert.equal(s.board[sq('b5')].t, 'p', 'у рівні 1 пішак не перетворюється');
  s = game(1, '5/1P3/5/3p1/5', 'b');
  s = move(s, 'd2d1');
  assert.equal(s.winner, 'b');
  assert.equal(s.reason, 'promote');
});

test('рівень 1: з’їв усіх — перемога', function () {
  var s = game(1, '5/5/1p3/2P2/5');
  s = move(s, 'c2b3');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'all');
});

test('рівень 1: суперникові нічим ходити — перемога', function () {
  var s = game(1, '5/p4/5/1P3/5');
  s = move(s, 'b2b3');
  assert.equal(s.over, false);
  s = game(1, '5/p4/5/P4/5');
  s = move(s, 'a2a3');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'nomoves');
});

test('рівень 1: повна коротка партія до заблокованих пішаків', function () {
  var s = MC.createGame(1);
  s = play(s, ['c2c3', 'b4c3', 'b2c3', 'd4c3', 'd2c3', 'e4e3']);
  assert.equal(s.over, false);
  s = move(s, 'a2a3');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'nomoves');
});

/* ---------- Рівні 2–5: фігура проти пішаків ---------- */

test('рівні 2–5: пішак на першому ряду втікає з дошки', function () {
  var s = game(2, '5/pp1pp/5/p4/2R2', 'b');
  s = move(s, 'a2a1');
  assert.equal(s.board[sq('a1')], null);
  assert.equal(s.escaped.b, 1);
  assert.equal(s.last.escaped, true);
  assert.equal(MC.score(s).escaped, 1);
  assert.equal(s.over, false);
});

test('рівень 2: тура з’їла 3 пішаки з 4 — перемога', function () {
  var s = MC.createGame(2);
  s.board[sq('a4')] = null;
  s.board[sq('b4')] = null;
  s.board[sq('d4')] = null;
  s.board[sq('c4')] = { c: 'b', t: 'p', id: 50 };
  s = move(s, 'c1c4');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'score');
  var sc = MC.score(s);
  assert.equal(sc.eaten, 3);
  assert.equal(sc.escaped, 0);
});

test('рівень 2: утекли 3 пішаки — перемога пішаків', function () {
  var s = MC.createGame(2, false, 'R4/5/5/1p3/5', 'b');
  s.start = { w: 1, b: 4 };
  s.escaped = { w: 0, b: 2 };
  s = move(s, 'b2b1');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'b');
  assert.equal(s.reason, 'score');
});

test('рівень 2: 2 на 2 — нічия', function () {
  var s = MC.createGame(2, false, 'R4/5/5/1p3/5', 'b');
  s.start = { w: 1, b: 4 };
  s.escaped = { w: 0, b: 1 };
  s = move(s, 'b2b1');
  assert.equal(s.over, true);
  assert.equal(s.winner, null);
  assert.equal(s.reason, 'score');
});

test('рівні 2–5: пішак з’їв фігуру — перемога пішаків', function () {
  var s = game(3, '5/pp1pp/5/1B3/5');
  s = move(s, 'b2c3');
  s = move(s, 'b4c3');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'b');
  assert.equal(s.reason, 'all');
});

test('рівень 4: заблоковані пішаки вважаються спійманими', function () {
  // Стартово 4 пішаки: 1 утік, 2 з'їдено, останній a2 кінь блокує з a1 — чорним нічим ходити.
  var s = MC.createGame(4, false, '5/5/1N3/p4/5', 'w');
  s.start = { w: 1, b: 4 };
  s.escaped = { w: 0, b: 1 };
  s = move(s, 'b3a1');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'score');
});

test('рівень 5: ферзь проти 5 пішаків — з’їв 3 — перемога', function () {
  var s = MC.createGame(5, false, '5/ppp2/1Q3/5/5');
  s.start = { w: 1, b: 5 };
  s.escaped = { w: 0, b: 0 };
  // Уже з'їдено 2 (на дошці 3 з 5), ферзь їсть третього.
  s = move(s, 'b3a4');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(MC.score(s).eaten, 3);
});

/* ---------- Рівень 6: король і пішаки ---------- */

test('рівень 6: з’їв короля — перемога', function () {
  var s = game(6, '5/5/2k2/1P3/2K2');
  s = move(s, 'b2c3');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'king');
});

test('рівень 6: дійшов пішаком — перемога', function () {
  var s = game(6, '2k2/P4/5/5/2K2');
  s = move(s, 'a4a5');
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'promote');
});

test('рівень 6: король може стати під удар (правило «з’їж короля»)', function () {
  var s = game(6, '2k2/5/1p3/5/2K2');
  assert.ok(MC.findMove(s, sq('c1'), sq('c2')), 'c2 б’є пішак b3, але хід дозволений');
  s = move(s, 'c1c2');
  s = move(s, 'b3c2');
  assert.equal(s.winner, 'b');
  assert.equal(s.reason, 'king');
});

test('рівень 6: лишились самі королі — нічия', function () {
  var s = game(6, '2k2/5/1p3/2K2/5');
  s = move(s, 'c2b3');
  assert.equal(s.over, true);
  assert.equal(s.winner, null);
  assert.equal(s.reason, 'kings');
});

/* ---------- Рівень 7: міні-шахи Гарднера ---------- */

test('рівень 7, для малят: з’їв короля — перемога, під шах ходити можна', function () {
  var s = game(7, 'k4/5/5/5/R3K');
  s = move(s, 'a1a5');
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'king');
  s = game(7, '4k/5/5/r4/K4');
  assert.ok(MC.findMove(s, sq('a1'), sq('b2')), 'b2 під ударом тури, але хід дозволений');
});

test('рівень 7, справжні правила: не можна лишити короля під шахом', function () {
  var s = game(7, '4k/5/5/r4/K4', 'w', true);
  var ms = names(MC.legalMoves(s), true);
  assert.deepEqual(ms, ['a1a2', 'a1b1']);
  s = game(7, 'r3k/5/5/1N3/K4', 'w', true);
  ms = names(MC.legalMoves(s), true);
  assert.ok(ms.indexOf('b2a4') >= 0, 'кінь закриває шах');
  assert.ok(ms.indexOf('b2d3') < 0, 'інші ходи коня не рятують від шаху');
});

test('рівень 7, справжні правила: мат — перемога, пат — нічия', function () {
  var s = game(7, 'k4/5/1K3/5/Q4', 'w', true);
  s = move(s, 'a1a4');
  assert.equal(s.over, true);
  assert.equal(s.winner, 'w');
  assert.equal(s.reason, 'mate');
  s = game(7, 'k4/5/1K3/5/2Q2', 'w', true);
  s = move(s, 'c1c4');
  assert.equal(s.over, true);
  assert.equal(s.winner, null);
  assert.equal(s.reason, 'stalemate');
});

test('рівень 7, справжні правила: кількість ходів зі старту (перевірено вручну)', function () {
  var s = MC.createGame(7, true);
  assert.equal(perft(s, 1), 7);
  assert.equal(perft(s, 2), 53);
});

test('довго без взяття й ходу пішака — нічия', function () {
  var s = game(7, 'k4/5/5/5/K1R2');
  var seq = ['c1c2', 'a5b5', 'c2c1', 'b5a5'];
  for (var i = 0; i < MC.QUIET_LIMIT; i++) {
    assert.equal(s.over, false);
    s = move(s, seq[i % 4]);
  }
  assert.equal(s.over, true);
  assert.equal(s.reason, 'quiet');
  assert.equal(s.winner, null);
});

test('після кінця гри ходів немає', function () {
  var s = game(1, '5/1P3/5/5/3p1');
  s = move(s, 'b4b5');
  assert.equal(MC.legalMoves(s).length, 0);
  assert.equal(MC.findMove(s, sq('d1'), sq('d2')), null);
});

/* ---------- Зірочки ---------- */

test('зірочки', function () {
  var s = MC.createGame(1);
  s.over = true;
  s.winner = 'w';
  assert.equal(MC.stars(s, 'bear'), 2);
  assert.equal(MC.stars(s, 'fox'), 3);
  assert.equal(MC.stars(s, 'teacher'), 3);
  s.board[sq('a2')] = null;
  assert.equal(MC.stars(s, 'fox'), 3, 'втрата 1 з 5 — ще чисто');
  s.board[sq('b2')] = null;
  assert.equal(MC.stars(s, 'fox'), 2);
  assert.equal(MC.stars(s, 'bear'), 1);
  s.winner = 'b';
  assert.equal(MC.stars(s, 'fox'), 0);
  var e = MC.createGame(2);
  e.over = true;
  e.winner = 'w';
  assert.equal(MC.stars(e, 'bear'), 2);
  e.escaped = { w: 0, b: 1 };
  assert.equal(MC.stars(e, 'bear'), 1, 'у рівнях 2–5 чисто — це жоден пішак не втік');
});

/* ---------- Боти ---------- */

test('Ведмедик: якщо можна з’їсти — їсть', function () {
  var s = game(1, '5/ppppp/1P3/5/5', 'b');
  for (var i = 0; i < 30; i++) {
    var m = MC.botBear(s, seeded(i + 1));
    assert.ok(m.capture);
  }
});

test('Ведмедик: інакше — будь-який дозволений хід', function () {
  var s = MC.createGame(7, true);
  for (var i = 0; i < 30; i++) {
    var m = MC.botBear(s, seeded(i + 7));
    assert.ok(MC.findMove(s, m.from, m.to));
  }
});

test('Лисичка: бачить виграш в один хід', function () {
  var s = game(1, 'ppp2/5/3P1/p4/5', 'b');
  var m = MC.botFox(s, seeded(3));
  assert.equal(MC.sqName(m.from) + MC.sqName(m.to), 'a2a1');
  s = game(7, 'k4/5/1K3/5/Q4', 'w', true);
  m = MC.botFox(s, seeded(4));
  assert.equal(MC.applyMove(s, m).reason, 'mate');
});

test('Лисичка: не віддає фігуру просто так', function () {
  // Тура на c3 під ударом пішака b4 і може піти; рушій не має лишатися під боєм.
  var s = game(2, '5/1p3/2R2/5/5', 'w');
  for (var i = 0; i < 10; i++) {
    var m = MC.botFox(s, seeded(i + 11));
    var ns = MC.applyMove(s, m);
    if (ns.over) continue;
    var replies = MC.legalMoves(ns).filter(function (r) { return r.capture; });
    assert.equal(replies.length, 0, 'після ходу ' + MC.sqName(m.from) + MC.sqName(m.to) + ' туру можна з’їсти');
  }
});

test('Лисичка відповідає менш ніж за 1 с на кожному рівні', function () {
  for (var lv = 1; lv <= 7; lv++) {
    [false, true].forEach(function (real) {
      if (real && lv !== 7) return;
      var s = MC.createGame(lv, real), rnd = seeded(lv * 31);
      for (var i = 0; i < 6 && !s.over; i++) {
        var t = Date.now();
        var m = MC.botFox(s, rnd);
        assert.ok(Date.now() - t < 1000, 'рівень ' + lv);
        s = MC.applyMove(s, m);
      }
    });
  }
});

test('випадкові партії на всіх рівнях закінчуються коректно', function () {
  var reasons = ['promote', 'king', 'all', 'nomoves', 'score', 'mate', 'stalemate', 'kings', 'quiet'];
  for (var lv = 1; lv <= 7; lv++) {
    for (var g = 0; g < 40; g++) {
      var real = lv === 7 && g % 2 === 1, s = MC.createGame(lv, real), rnd = seeded(lv * 1000 + g);
      for (var ply = 0; ply < 500 && !s.over; ply++) {
        var ms = MC.legalMoves(s);
        assert.ok(ms.length > 0, 'гра без ходів має бути завершена');
        var m = ms[Math.floor(rnd() * ms.length)];
        if (real) assert.equal(MC.inCheck(MC.applyMove(s, m).board, s.turn), false, 'свій король під шахом');
        s = MC.applyMove(s, m);
      }
      assert.equal(s.over, true, 'рівень ' + lv + ' партія ' + g + ' не закінчилась');
      assert.ok(reasons.indexOf(s.reason) >= 0);
      if (s.rules.promo !== 'queen') {
        s.board.forEach(function (p, i) {
          if (p && p.t === 'q') assert.ok(lv === 5 && p.c === 'w', 'зайвий ферзь на ' + MC.sqName(i));
        });
      }
    }
  }
});
