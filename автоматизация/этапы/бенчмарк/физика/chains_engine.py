"""Цепочки формул: многошаговые расчётные задачи из формулы.json среза, глубина — из нормы ФОП.

Модуль без привязки к прогону (его использует и этап «цепочки», и сборка заданий), поэтому всё получает аргументами:
  forms  — формулы.json среза (формулы, величины, обозначения);
  isa    — {понятие: множество его родов по графу} (is_a), чтобы «Сила тяжести» могла встать на место «Силы»;
  ref    — справочные_данные.json (g, плотности);
  manual — роли величин, условия, наборы значений и решения «ок/нет». Их готовят этапы цепочки_роли_llm и цепочки_llm
           (решения/цепочки_роли.json, решения/цепочки.json); ручные_данные/физика/<срез>/цепочки.json, если есть, — правки поверх.

Идея. Каждая формула — уравнение. Величина в формуле — «слот» = (понятие, объект): «плотность» тела и «плотность»
жидкости в законе Архимеда — разные величины, хотя буква одна. Цепочка — дерево: корневая формула решается относительно
искомой величины, одна из её данных заменяется другой формулой, решённой относительно этой величины, и т. д.
Число формул в дереве = число действий (шагов). Числа подбирает программа «снизу вверх», ответ считает она же.

Чего модуль не решает: осмысленна ли цепочка физически. Это решает модель-проверяющий (этап цепочки_llm):
в задания попадают только наборы с решением «ок».
"""
import hashlib
import math
import random
import re

import sympy as sp

SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SYM_RE = re.compile(r"Δ?[A-Za-zα-ωΑ-Ω][а-яё]{0,2}[₀-₉]*")

# Наборы «круглых» значений по единице СИ (если для понятия не задан свой набор в цепочки.json).
UNIT_VALUES = {
    "м": [0.2, 0.5, 1, 1.5, 2, 3, 4, 5, 10],
    "с": [2, 4, 5, 10, 20, 30, 60, 120],
    "м/с": [2, 3, 4, 5, 10, 15, 20],
    "кг": [0.2, 0.5, 1, 2, 3, 5, 10, 20, 50],
    "м³": [0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02],
    "м²": [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1],
    "Н": [1, 2, 4, 5, 10, 20, 50, 100, 200, 500],
    "Па": [500, 1000, 2000, 5000, 10000, 20000, 50000],
    "Дж": [10, 20, 50, 100, 200, 500, 1000],
    "Вт": [10, 20, 50, 100, 200, 500, 1000],
    "Н/м": [20, 40, 50, 100, 200, 500],
    "Н·м": [1, 2, 5, 10, 20, 50],
    "%": [40, 50, 60, 70, 75, 80, 90],
}


def norm_steps(text):
    """Норма числа действий из текста умения «решать расчётные задачи…»: (мин, макс) или None, если числом не задана."""
    t = (text or "").lower()
    m = re.search(r"(\d)\s*[–\-]\s*(\d)\s*(действ|уравнен)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"в\s*(\d)\s*действ", t)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None


def fmt(x):
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}".replace(",", " ")
    s = f"{x:.6g}"
    if "e" in s:
        s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def nice(x, digits=3):
    return x > 0 and math.isfinite(x) and abs(float(f"{x:.{digits}g}") - x) <= 1e-9 * max(1, abs(x))


class Slot:
    __slots__ = ("sym", "concept", "obj", "unit", "name", "const", "vals")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def key(self):
        return (self.concept, self.obj)


class Formula:
    def __init__(self, f, forms, roles, const_concepts):
        self.id, self.text, self.concepts = f["id"], f["text"], f.get("concepts") or []
        self.ok, self.why = True, ""
        txt = self.text.replace("·", "*").replace("−", "-").replace("%", "").replace("²", "**2").replace("³", "**3")
        if txt.count("=") != 1:
            self.ok, self.why = False, "не одно «=»"
            return
        toks = sorted(set(SYM_RE.findall(txt)), key=lambda t: (-len(t), t))  # при равной длине — по алфавиту: порядок не зависит от PYTHONHASHSEED
        safe = {}
        for i, t in enumerate(toks):
            safe[t] = f"x{i}"
        body = txt
        for t in toks:  # длинные первыми, через маркеры, чтобы «F» не съело «F₁»
            body = re.sub(r"(?<![\wА-я])" + re.escape(t) + r"(?![\wА-я₀-₉])", "\u0001" + safe[t] + "\u0002", body)
        body = body.replace("\u0001", "").replace("\u0002", "")
        lhs, rhs = body.split("=")
        loc = {v: sp.Symbol(v, positive=True) for v in safe.values()}
        try:
            self.eq = sp.sympify(lhs, locals=loc) - sp.sympify(rhs, locals=loc)
        except Exception as e:  # noqa: BLE001
            self.ok, self.why = False, f"не разобрать: {e}"
            return
        rev = {v: k for k, v in forms.get("обозначения", {}).items()}
        quant = forms.get("величины", {})
        lhs_toks = SYM_RE.findall(self.text.split("=")[0])
        single_lhs = len(lhs_toks) == 1
        self.slots, self.sym = {}, {}
        r_over = (roles or {}).get(self.id, {})
        for t in toks:
            base = t.translate(SUB)
            idx = re.sub(r"\D", "", base)
            base_nodig = re.sub(r"\d", "", base)
            if single_lhs and t == lhs_toks[0] and self.concepts:
                qs = [c for c in self.concepts if c in forms.get("единицы", {})]
                concept, obj = (qs or self.concepts)[0], "тело"
            elif t in rev:
                concept, obj = rev[t], "тело"
            elif base_nodig in rev:
                concept, obj = rev[base_nodig], idx or "тело"
            elif base_nodig[:-1] in rev or base_nodig[:-2] in rev:  # Aп, Aз → работа (полезная/затраченная)
                stem = base_nodig[:-1] if base_nodig[:-1] in rev else base_nodig[:-2]
                concept, obj = rev[stem], base_nodig[len(stem):] + idx
            elif base_nodig in quant:
                concept, obj = quant[base_nodig]["name"], idx or "тело"
            else:
                concept, obj = t, "тело"
            ov = r_over.get(t, {})
            concept, obj = ov.get("понятие", concept), ov.get("объект", obj)
            ub = re.sub(r"[а-яё]+$", "", base_nodig) or base_nodig
            # явно заданная единица (в т. ч. пустая — безразмерная величина, например КПД в долях единицы) важнее справочника
            unit = ov["единица"] if "единица" in ov else (quant.get(base_nodig) or quant.get(ub) or {}).get("unit", "")
            name = ov.get("назв") or (concept[0].lower() + concept[1:] if concept[:2] != concept[:2].upper() else concept)
            if obj != "тело" and not ov.get("назв"):
                name += f" ({obj})"
            vals = [float(x) for x in ov.get("значения") or [] if isinstance(x, (int, float)) and x > 0] or None
            s = Slot(sym=t, concept=concept, obj=obj, unit=unit, name=name, const=concept in const_concepts, vals=vals)
            self.slots[t] = s
            self.sym[t] = loc[safe[t]]
        self._solved = {}
        for t in self.slots:
            try:
                sol = sp.solve(self.eq, self.sym[t])
                if sol:
                    self._solved[t] = sp.lambdify([self.sym[u] for u in self.slots if u != t], sol[0], "math")
            except Exception:  # noqa: BLE001  (в т. ч. SyntaxError из lambdify: «Q₁ + Q₂ + … + Qₙ = 0» — схема, а не расчётная формула)
                pass
        if len(self._solved) < len(self.slots):
            self.ok, self.why = False, "не решается относительно всех величин"

    def solve(self, target, values):
        """values: {символ: число} для всех величин, кроме target."""
        f = self._solved[target]
        args = [values[u] for u in self.slots if u != target]
        return float(f(*args))


def _fits(supply, need, isa):
    """Величина supply (вычисленная) подходит на место need: тот же объект и то же понятие или его вид."""
    return supply.obj == need.obj and (supply.concept == need.concept or need.concept in isa.get(supply.concept, set()))


class Chain:
    """Дерево: node = (formula_id, solved_sym, {sym_данного: дочерний node})."""

    def __init__(self, root):
        self.root = root

    def nodes(self, n=None):
        n = n or self.root
        yield n
        for ch in n[2].values():
            yield from self.nodes(ch)

    @property
    def size(self):
        return sum(1 for _ in self.nodes())

    def formulas(self):
        return [n[0] for n in self.nodes()]

    def cid(self, F):
        f, t, _ = self.root
        key = f + ":" + t + "|" + ";".join(sorted(f"{n[0]}:{n[1]}" for n in self.nodes()))
        return "CH." + F[f].slots[t].concept.replace(" ", "_")[:24] + "." + hashlib.sha1(key.encode()).hexdigest()[:6]


def group_id(F, formulas):
    """Набор формул цепочки — единица просмотра человеком: «ок» на набор открывает все его цепочки (разные искомые)."""
    key = "+".join(sorted(formulas))
    return "N." + hashlib.sha1(key.encode()).hexdigest()[:8]


class Engine:
    def __init__(self, forms, isa, ref, manual=None):
        manual = manual or {}
        self.ref, self.manual = ref or {}, manual
        self.const = {"Ускорение свободного падения"}
        self.F = {}
        self.skipped = {}
        for f in forms.get("формулы", []):
            if f["id"] in set(manual.get("исключить_формулы", [])):
                self.skipped[f["id"]] = "исключена вручную"
                continue
            fo = Formula(f, forms, manual.get("роли", {}), self.const)
            if fo.ok:
                self.F[f["id"]] = fo
            else:
                self.skipped[f["id"]] = fo.why
        self.isa = isa
        self.values = manual.get("значения", {})
        self.conditions = manual.get("условия", {})

    # ───── перебор ─────
    def enumerate(self, max_steps, min_steps=2):
        out, seen = [], set()
        frontier = []
        for fid, fo in self.F.items():
            for t, s in fo.slots.items():
                if not s.const:
                    frontier.append(Chain((fid, t, {})))
        for c in frontier:
            self._grow(c, max_steps, min_steps, out, seen)
        return out

    def _key(self, c):
        return tuple(sorted(f"{n[0]}:{n[1]}" for n in c.nodes())) + (c.root[0] + ":" + c.root[1],)

    def _grow(self, c, max_steps, min_steps, out, seen):
        k = self._key(c)
        if k in seen:
            return
        seen.add(k)
        if c.size >= min_steps and self._consistent(c):
            out.append(c)
        if c.size >= max_steps:
            return
        used = set(c.formulas())
        for node in list(c.nodes()):
            fo = self.F[node[0]]
            for gsym, need in fo.slots.items():
                if gsym == node[1] or gsym in node[2] or need.const:
                    continue
                for fid2, fo2 in self.F.items():
                    if fid2 in used:
                        continue
                    for t2, sup in fo2.slots.items():
                        if sup.const or not _fits(sup, need, self.isa):
                            continue
                        self._grow(Chain(self._attach(c.root, node, gsym, (fid2, t2, {}))), max_steps, min_steps, out, seen)

    def _attach(self, cur, target, gsym, new):
        f, t, ch = cur
        ch2 = {g: self._attach(x, target, gsym, new) for g, x in ch.items()}
        if cur is target:
            ch2[gsym] = new
        return (f, t, ch2)

    def roles_of(self, c):
        """-> (вычисляемые величины, данные величины) как множества ключей (понятие, объект)."""
        comp, given = [], []

        def walk(n):
            fo = self.F[n[0]]
            comp.append(fo.slots[n[1]].key)
            for g, s in fo.slots.items():
                if g == n[1]:
                    continue
                if g in n[2]:
                    walk(n[2][g])
                else:
                    given.append(s.key)
        walk(c.root)
        return comp, given

    def _consistent(self, c):
        comp, given = self.roles_of(c)
        if len(comp) != len(set(comp)):
            return False
        gs = set(given)
        if gs & set(comp):          # величина и дана, и вычисляется — задача переопределена
            return False
        root = self.F[c.root[0]].slots[c.root[1]]
        # род/вид: «сила тяжести» вычисляется, а «сила» тела дана — тоже переопределение
        for ck in comp:
            for gk in gs:
                if ck[1] == gk[1] and (gk[0] in self.isa.get(ck[0], set()) or ck[0] in self.isa.get(gk[0], set())):
                    return False
        return not root.const

    # ───── числа и текст ─────
    def _pick(self, slot, r):
        if slot.const:
            g = self.ref.get("g", {})
            return (g.get("value", 10) if isinstance(g, dict) else g), None
        vals = slot.vals or self.values.get(slot.concept) or self.values.get(slot.name)
        if not vals and slot.unit == "кг/м³":
            tab = self.ref.get("плотность_жидкостей" if slot.obj == "жидкость" else "плотность_твёрдых") or {}
            if tab:
                sub = r.choice(sorted(tab))
                return tab[sub], sub
        vals = vals or UNIT_VALUES.get(slot.unit) or [1, 2, 3, 4, 5, 10]
        return r.choice(vals), None

    def plausible(self, slot, x):
        """Вычисленная величина в разумных пределах: для плотности — в пределах таблицы, иначе в 5 раз от набора значений."""
        if slot.unit == "кг/м³":   # плотность — только в пределах реальных веществ
            tab = slot.vals or self.values.get(slot.concept) or list((self.ref.get(
                "плотность_жидкостей" if slot.obj == "жидкость" else "плотность_твёрдых") or {}).values())
            if tab:
                return min(tab) * 0.9 <= x <= max(tab) * 1.1
        vals = slot.vals or self.values.get(slot.concept) or self.values.get(slot.name) or UNIT_VALUES.get(slot.unit)
        if not vals:
            return True
        return min(vals) / 5 <= x <= max(vals) * 5

    def instance(self, c, seed, tries=400):
        """Числа для цепочки: -> dict(question, answer, unit, steps, explanation, givens) или None."""
        for i in range(tries):
            r = random.Random(f"{seed}#{i}")
            res = self._try(c, r)
            if res and nice(res["answer"], 3) and all(nice(v, 4) for v in res["inter"]):
                return res
        for i in range(tries):  # смягчённо: 4 значащие цифры
            res = self._try(c, random.Random(f"{seed}~{i}"))
            if res and nice(res["answer"], 4):
                return res
        return None

    def _try(self, c, r):
        given_txt, steps, inter, links = [], [], [], []
        given_seen = {}

        def ev(n):
            fo = self.F[n[0]]
            vals = {}
            for g, s in fo.slots.items():
                if g == n[1]:
                    continue
                if g in n[2]:
                    vals[g] = ev(n[2][g])
                    ch = n[2][g]
                    sup = self.F[ch[0]].slots[ch[1]]
                    if sup.concept != s.concept:   # «сила» в работе — это сила тяжести: сказать в условии
                        links.append(f"«{s.name[0].upper() + s.name[1:]}» здесь — {sup.name}.")
                else:
                    if s.key in given_seen:
                        vals[g] = given_seen[s.key]
                        continue
                    v, sub = self._pick(s, r)
                    given_seen[s.key] = v
                    vals[g] = v
                    if s.const:
                        given_txt.append(f"примите g = {fmt(v)} {s.unit}")
                    else:
                        extra = f" ({sub})" if sub else ""
                        given_txt.append(f"{s.name}{extra} — {fmt(v)} {s.unit}".rstrip())
            try:
                x = fo.solve(n[1], vals)
            except (ValueError, ZeroDivisionError, OverflowError, TypeError):
                return float("nan")
            t = fo.slots[n[1]]
            if math.isfinite(x) and not self.plausible(t, x):
                x = float("nan")
            steps.append(f"{t.sym} из {fo.text}: {t.sym} = {fmt(x) if math.isfinite(x) else '?'} {t.unit}".rstrip())
            inter.append(x)
            return x
        ans = ev(c.root)
        if not (math.isfinite(ans) and ans > 0) or any(not (math.isfinite(x) and x > 0) for x in inter):
            return None
        if any(abs(ans - v) <= 1e-9 * max(1, abs(v)) for v in given_seen.values()):
            return None   # ответ совпал с одним из данных — вырожденный случай (S₂ = S₁)
        root = self.F[c.root[0]].slots[c.root[1]]
        conds = []
        for fid in c.formulas():
            if self.conditions.get(fid) and self.conditions[fid] not in conds:
                conds.append(self.conditions[fid])
        consts = [x for x in given_txt if x.startswith("примите")]
        plain = [x for x in given_txt if not x.startswith("примите")]
        q = ("Известно: " + "; ".join(plain) + ". " + (" ".join(conds + links) + " " if conds or links else "") +
             (consts[0][0].upper() + consts[0][1:] + ". " if consts else "") +
             f"Найдите: {root.name}." + (f" Ответ дайте в {root.unit}." if root.unit else ""))
        return dict(question=q, answer=float(f"{ans:.6g}"), unit=root.unit, steps=len(inter),
                    explanation="; ".join(f"{i + 1}) {s}" for i, s in enumerate(steps)), inter=inter[:-1])

    def describe(self, c):
        """Схема цепочки для человека: «Выталкивающая сила ← Объём (из Плотность вещества)»."""
        def d(n):
            fo = self.F[n[0]]
            s = f"{fo.slots[n[1]].name} [{fo.text}]"
            if n[2]:
                s += " ← " + ", ".join(d(x) for x in n[2].values())
            return s
        return d(c.root)
