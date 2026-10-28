"""История: даты, века, проверка дат по цитатам (общая часть для этапов «знания_llm», «знания» и сборки заданий).

Дата в знании вида «дата» записывается объектом «даты»:
  {"что": "событие" | "период" | "правление" | "жизнь" | "деятельность" | "создание" | "существование",
   "точность": "год" | "век",
   "начало": 1237, "конец": 1240}          — для точности «год»; годы до н. э. — отрицательные (−490 = 490 г. до н. э.)
   "век": 13  или  [9, 10]                  — для точности «век»; века до н. э. — отрицательные (−5 = V в. до н. э.)
Года 0 нет: за 1 г. до н. э. сразу идёт 1 г. н. э.
Проверка: каждый год (или римская цифра века) должен стоять в одной из цитат знания; у дат до н. э. в цитате должно быть «до н. э.».
"""
import re

WHAT = {"событие", "период", "правление", "жизнь", "деятельность", "создание", "существование"}
PRECISION = {"год", "век"}

_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
          (5, "V"), (4, "IV"), (1, "I")]


def roman(n):
    out = ""
    for v, s in _ROMAN:
        while n >= v:
            out += s
            n -= v
    return out


def century(y):
    """Век года: 1380 → 14, 1700 → 17, −490 → −5 (V в. до н. э.)."""
    return (y - 1) // 100 + 1 if y > 0 else -((-y - 1) // 100 + 1)


def millennium(y):
    return (y - 1) // 1000 + 1 if y > 0 else -((-y - 1) // 1000 + 1)


def century_bounds(c):
    """Годы века: 14 → (1301, 1400); −5 → (−500, −401)."""
    return (100 * (c - 1) + 1, 100 * c) if c > 0 else (-100 * -c, -100 * (-c - 1) - 1)


def fmt_century(c):
    return f"{roman(abs(c))} в." + (" до н. э." if c < 0 else "")


def fmt_millennium(m):
    return f"{roman(abs(m))} тысячелетие" + (" до н. э." if m < 0 else " н. э.")


def fmt_year(y):
    return f"{y} г." if y > 0 else f"{-y} г. до н. э."


def fmt_span(a, b):
    if a == b:
        return fmt_year(a)
    if a < 0 and b < 0:
        return f"{-a}–{-b} гг. до н. э."
    if a < 0 < b:
        return f"{-a} г. до н. э. – {b} г. н. э."
    return f"{a}–{b} гг."


def step(c, k):
    """Сдвиг века (тысячелетия) на k без нулевого: −1 + 1 → 1."""
    for _ in range(abs(k)):
        c = c + (1 if k > 0 else -1)
        if c == 0:
            c = 1 if k > 0 else -1
    return c


def years_between(y1, y2):
    """Сколько лет прошло от y1 до y2 (y1 ≤ y2); через рубеж эр — без нулевого года."""
    d = y2 - y1
    return d - 1 if y1 < 0 < y2 else d


def span_of(d):
    """(начало, конец, точность) по объекту «даты»; None — если объект неверный."""
    if not isinstance(d, dict):
        return None
    if d.get("точность") == "год":
        a, b = d.get("начало"), d.get("конец", d.get("начало"))
        if b is None:
            b = a
        if not isinstance(a, int) or not isinstance(b, int):
            return None
        return (a, b, "год")
    if d.get("точность") == "век":
        v = d.get("век")
        vs = v if isinstance(v, list) else [v]
        if not vs or not all(isinstance(x, int) and x != 0 for x in vs):
            return None
        bs = [century_bounds(x) for x in vs]
        return (min(b[0] for b in bs), max(b[1] for b in bs), "век")
    return None


def gap(s1, s2):
    """Сколько лет между двумя промежутками (0 — пересекаются)."""
    return max(0, s2[0] - s1[1], s1[0] - s2[1])


def _norm(t):
    return re.sub(r"\s+", " ", (t or "").replace("ё", "е").replace(" ", " ")).lower()


def check_dates(d, quotes):
    """Ошибки в объекте «даты» знания (quotes — тексты цитат этого знания)."""
    if not isinstance(d, dict):
        return ["у знания вида «дата» нужен объект «даты» (что, точность, начало/конец или век)"]
    errs = []
    if d.get("что") not in WHAT:
        errs.append(f"даты.что — одно из: {', '.join(sorted(WHAT))}")
    if d.get("точность") not in PRECISION:
        return errs + ["даты.точность — «год» или «век»"]
    sp = span_of(d)
    if sp is None:
        return errs + (["даты: для точности «год» нужны целые «начало» и «конец» (до н. э. — отрицательные)"] if d["точность"] == "год"
                       else ["даты: для точности «век» нужен «век» — целое число или список (до н. э. — отрицательные, нуля нет)"])
    a, b, _ = sp
    if a > b:
        errs.append("даты: начало позже конца (годы до н. э. — отрицательные: 490 г. до н. э. = −490)")
    if d["точность"] == "год" and (0 in (a, b) or abs(a) > 2100 or abs(b) > 2100):
        errs.append("даты: года 0 нет; год — от −2100 до 2100 (раньше — пишите век)")
    text = " ".join(_norm(q) for q in quotes)
    if not text:
        return errs + ["даты: дата подтверждается только цитатой — нужна хотя бы одна цитата с этим годом (веком)"]
    if d["точность"] == "год":
        for y in sorted({a, b}):
            if not re.search(rf"(?<!\d){abs(y)}(?!\d)", text):
                errs.append(f"даты: года {abs(y)} нет ни в одной цитате этого знания — дата должна стоять в цитате дословно")
    else:
        v = d["век"] if isinstance(d["век"], list) else [d["век"]]
        toks = roman_tokens(text)
        for c in v:
            r = roman(abs(c))
            if r.lower() not in toks and not re.search(rf"(?<!\d){abs(c)}(?!\d)\s*(-?й|-?м)?\s*век", text):
                errs.append(f"даты: века {r} нет ни в одной цитате этого знания")
    if (a < 0 or b < 0) and not re.search(r"до\s*н\.?\s*э|до нашей эры", text):
        errs.append("даты: дата до н. э. — в цитате должно быть «до н. э.» (иначе непонятно, какая эра)")
    return errs


def roman_tokens(text):
    """Римские числа в тексте (строчные). Кириллическая «х» в «ХХ в.» считается латинской x."""
    return {t.replace("х", "x") for t in re.findall(r"(?<![а-яa-z])[ivxlcdmх]+(?![а-яa-z])", _norm(text))}


def label_stems(label):
    """Основы слов названия (первые 5 букв слов от 4 букв) — чтобы проверить, что в «сути» не названо само понятие."""
    return {w[:5] for w in re.findall(r"[а-яa-z]+", _norm(label)) if len(w) >= 4}


def names_concept(text, labels):
    """Текст называет понятие: в нём есть все основы хотя бы одного из названий (с основой от 4 букв)."""
    t = _norm(text)
    for lab in labels:
        st = label_stems(lab)
        if st and all(x in t for x in st):
            return True
    return False
