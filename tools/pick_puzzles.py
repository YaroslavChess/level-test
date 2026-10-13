#!/usr/bin/env python3
"""Відбір задач для тесту з відкритої бази Lichess (CC0).

Використання:
    python3 -I tools/pick_puzzles.py <lichess_db_puzzle.csv[.zst]> [вихідний.js]

База: https://database.lichess.org/#puzzles (ліцензія CC0).
Для .zst потрібен пакет `zstandard` (pip install zstandard).

Скрипт нічого не вигадує: він лише фільтрує рядки бази й виводить їх як
JavaScript-масив PUZZLES для вставки в index.html. Поля: id, fen, moves,
rating, themes. Нічого не змінюється, лише переписується з бази.
"""
import csv
import io
import sys

MIN_RATING = 500
MAX_RATING = 2000
STEP = 50                 # крок рейтингу між «кошиками» задач
PER_BUCKET = 3            # скільки задач брати на кожен «кошик» (запас для адаптивності)
WINDOW = 12               # допустиме відхилення від цільового рейтингу
# Суворі критерії. Для низьких рейтингів задач із такою кількістю розв'язань
# мало, тому діє другий, м'якший рівень (використовується лише коли суворих нема).
MIN_POPULARITY = 92       # відсоток «лайків» серед гравців
MIN_PLAYS = 20000         # скільки разів задачу вже розв'язували
MAX_RATING_DEVIATION = 85 # надійність рейтингу задачі
RELAXED_POPULARITY = 85
RELAXED_PLAYS = 2000
RELAXED_RATING_DEVIATION = 110
MAX_MOVES = 6             # перший хід суперника + до 3 ходів розв'язувача
EXCLUDE_THEMES = set(["veryLong", "long", "enPassant", "castling"])


def open_text(path):
    if path.endswith(".zst"):
        import zstandard
        raw = open(path, "rb")
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        return io.TextIOWrapper(reader, encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    targets = list(range(MIN_RATING, MAX_RATING + 1, STEP))
    buckets = {}
    for t in targets:
        buckets[t] = []

    reader = csv.DictReader(open_text(src))
    for row in reader:
        rating = int(row["Rating"])
        if rating < MIN_RATING - WINDOW or rating > MAX_RATING + WINDOW:
            continue
        popularity = int(row["Popularity"])
        plays = int(row["NbPlays"])
        deviation = int(row["RatingDeviation"])
        if popularity < RELAXED_POPULARITY or plays < RELAXED_PLAYS or deviation > RELAXED_RATING_DEVIATION:
            continue
        strict = popularity >= MIN_POPULARITY and plays >= MIN_PLAYS and deviation <= MAX_RATING_DEVIATION
        moves = row["Moves"].split()
        if len(moves) > MAX_MOVES or len(moves) % 2 != 0:
            continue
        themes = row["Themes"].split()
        if EXCLUDE_THEMES.intersection(themes):
            continue
        nearest = int(round(rating / float(STEP))) * STEP
        if nearest not in buckets or abs(rating - nearest) > WINDOW:
            continue
        buckets[nearest].append({
            "id": row["PuzzleId"],
            "fen": row["FEN"],
            "moves": moves,
            "rating": rating,
            "themes": themes,
            "popularity": popularity,
            "plays": plays,
            "strict": strict,
        })

    used_themes = {}
    picked = []
    for t in targets:
        cands = buckets[t]
        if not cands:
            sys.stderr.write("Немає кандидатів для %d\n" % t)
            continue
        for _ in range(PER_BUCKET):
            if not cands:
                break
            # Спершу суворі критерії, потім ті, чия головна тема траплялась рідше,
            # потім за популярністю й кількістю розв'язань.
            def score(p):
                main = p["themes"][0] if p["themes"] else ""
                return (not p["strict"], used_themes.get(main, 0), -p["popularity"], -p["plays"])
            cands.sort(key=score)
            best = cands.pop(0)
            main = best["themes"][0] if best["themes"] else ""
            used_themes[main] = used_themes.get(main, 0) + 1
            picked.append(best)
    picked.sort(key=lambda p: p["rating"])

    lines = []
    lines.append("var PUZZLES = [")
    for p in picked:
        lines.append("  { id: \"%s\", rating: %d, fen: \"%s\"," % (p["id"], p["rating"], p["fen"]))
        lines.append("    moves: \"%s\" }," % " ".join(p["moves"]))
    lines.append("];")
    text = "\n".join(lines) + "\n"
    if out_path:
        open(out_path, "w", encoding="utf-8").write(text)
    else:
        sys.stdout.write(text)
    sys.stderr.write("Відібрано задач: %d\n" % len(picked))
    for p in picked:
        sys.stderr.write("%s %d %s (%d%%, %d%s)\n" % (
            p["id"], p["rating"], ",".join(p["themes"][:3]), p["popularity"], p["plays"],
            "" if p["strict"] else ", м'які критерії"))


main()
