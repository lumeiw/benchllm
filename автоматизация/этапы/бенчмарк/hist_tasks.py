"""Семейства заданий истории (этап «задания», вызываются из bench_tasks.py, если «предмет»: «history»).

Правильный ответ — из ребра графа, знания с цитатой или даты знания (дата проверена по цитате на этапе «знания»).
Неверные варианты подбирает программа, и для истории к общим правилам (не сосед по графу, не род и не вид, не часть)
добавлены два своих:
  • время: если даты известны, неверный вариант отстоит от ответа на N лет (деятель — с запасом на годы жизни),
    без дат — берётся из другого раздела программы;
  • соучастники и части одного целого (деятели одного события, сражения одной войны) неверным вариантом друг к другу не ставятся.

  H1  дата / хронологические рамки           H2  событие по дате          H3  век      H3m  тысячелетие   H3h  часть века
  H4  последовательность (порядок)           H5  сколько лет прошло       H6  современники
  H7  синхронность событий истории России и всеобщей истории (нужны «курсы» в конфиге среза)
  U1  участник события                       U2  с чем связана деятельность деятеля
  U3  место события                          U4  с кем / чем связан документ или памятник культуры
  Y1  группировка: какие события относятся к процессу (войне, периоду)
  D1  итог  D2  причина  D3  деятельность деятеля  D4  о ком идёт речь  D5  значение, черты, содержание документа, описание памятника
"""
from collections import Counter, defaultdict

from bench_common import rng, slug
from hist_common import (century, fmt_century, fmt_millennium, fmt_span, fmt_year, gap, millennium, names_concept, span_of, step,
                         years_between)

FAMILY_HISTORY = {
    "G1": ("Вид понятия", "содержание"), "G1m": ("Виды понятия (несколько)", "содержание"), "G2": ("Часть целого", "содержание"),
    "Y1": ("Группировка: что относится к событию, процессу", "содержание"),
    "H1": ("Дата, хронологические рамки", "содержание"), "H2": ("Событие по дате", "содержание"),
    "H3": ("Век", "содержание"), "H3m": ("Тысячелетие", "содержание"), "H3h": ("Часть века", "содержание"),
    "H4": ("Последовательность событий", "содержание"), "H5": ("Сколько лет прошло (счёт лет)", "содержание"),
    "H6": ("Современники", "содержание"), "H7": ("Синхронность: история России и всеобщая история", "содержание"),
    "U1": ("Участник события", "содержание"), "U2": ("С чем связана деятельность деятеля", "содержание"),
    "U3": ("Место события", "содержание"), "U4": ("Документ, памятник культуры: с кем, с чем связан", "содержание"),
    "K1": ("Выбрать определение", "содержание"), "K2": ("Назвать понятие по определению", "содержание"),
    "K3": ("Дать определение (развёрнуто)", "содержание"),
    "D1": ("Итог (результат)", "содержание"), "D2": ("Причина (предпосылка)", "содержание"),
    "D3": ("Деятельность исторического деятеля", "содержание"), "D4": ("О ком идёт речь", "содержание"),
    "D5": ("Значение, черты, содержание документа, описание памятника", "содержание"),
    "K5": ("Два верных утверждения", "содержание"), "K6": ("О чём (о ком) идёт речь: описание", "содержание"),
    "K7": ("Объяснить (развёрнуто): причины, следствия, значение, сравнение", "содержание"),
    "K8": ("Найти ошибочное утверждение", "содержание"),
    "P": ("Прочее: факты и мнения, оценки, фальсификация, взаимосвязь эпох", "содержание"),
    "S1": ("Что нужно знать до (структура)", "структура"), "S2": ("Порядок изучения (структура)", "структура"),
}

EVENTS = ("событие",)
EVENT_LIKE = ("событие", "процесс", "период")
DATED_THINGS = ("событие", "процесс", "период", "документ", "памятник_культуры")
PERSON = "личность"
PRIORITY = {PERSON: ("жизнь", "правление", "деятельность")}
PRIORITY_DEFAULT = ("событие", "период", "создание", "существование", "правление", "деятельность", "жизнь")
FAR_PERSON = 60        # годы правления или деятельности — не вся жизнь: запас с каждой стороны
GAP_DATE = 3           # неверная дата отстоит от верной хотя бы на столько лет
GAP_TIME = 30          # «далеко по времени» для неверных вариантов-деятелей и событий
GAP_SYNC = 20          # синхронность: неверное событие отстоит от данного хотя бы на столько лет


class History:
    def __init__(self, b):
        self.b = b
        self.C, self.L = b.C, b.L
        self.reqs = b.srez["requirements"]
        self.D = defaultdict(dict)             # cid → {что: (начало, конец, точность, id знания, текст)}
        for k in b.know:
            if k.get("dates") and k["status"] in ("подтверждено", "частично"):
                sp = span_of(k["dates"])
                if sp:
                    self.D[k["concept_id"]].setdefault(k["dates"]["что"], (*sp, k["id"], k["text"]))
        self.ess = defaultdict(dict)           # вид → {cid: знание с «сутью»}
        for k in b.know:
            if k.get("essence") and k["status"] in ("подтверждено", "частично"):
                self.ess[k["kind"]].setdefault(k["concept_id"], k)
        self.part = defaultdict(set)           # участник → события, процессы, государства
        self.parts_of = defaultdict(set)       # событие → его участники
        for e in b.edges["participant_of"]:
            self.part[e["source"]].add(e["target"])
            self.parts_of[e["target"]].add(e["source"])

    # ───── даты ─────
    def date(self, cid, kinds=None):
        d = self.D.get(cid, {})
        for w in kinds or PRIORITY.get(self.C[cid]["ctype"], PRIORITY_DEFAULT):
            if w in d:
                return w, d[w]
        return None

    def span(self, cid):
        g = self.date(cid)
        return (g[1][0], g[1][1]) if g else None

    def wide(self, cid):
        """Промежуток, в который точно попадает деятельность: у деятеля без годов жизни — с запасом."""
        g = self.date(cid)
        if not g:
            return None
        a, b_ = g[1][0], g[1][1]
        if self.C[cid]["ctype"] == PERSON and g[0] != "жизнь":
            return (a - FAR_PERSON, b_ + FAR_PERSON)
        return (a, b_)

    def far(self, c1, c2, n=GAP_TIME):
        """True — далеко по времени; False — близко; None — у одного из понятий нет даты."""
        s1, s2 = self.wide(c1), self.wide(c2)
        if not s1 or not s2:
            return None
        return gap(s1, s2) >= n

    def safe(self, right, cand, n=GAP_TIME):
        """Можно ли поставить cand неверным вариантом к ответу right: далеко по времени, а без дат — другой раздел."""
        f = self.far(right, cand, n)
        if f is not None:
            return f
        return self.C[right]["section"] != self.C[cand]["section"]

    def role(self, *roles):
        from bench_common import first_role
        return first_role(self.reqs, *roles) or roles[0]

    def lab(self, cid):
        return self.C[cid]["label"]

    def choose(self, r, pool, k, key=None):
        """k вариантов из pool: сначала ближайшие по key (правдоподобные), из 2k ближайших — случайно."""
        pool = list(dict.fromkeys(pool))
        if key:
            pool.sort(key=key)
            top = pool[:max(2 * k, k + 3)]
        else:
            top = pool
        r.shuffle(top)
        return top[:k]

    def excl(self, *cids):
        out = set(cids)
        for c in cids:
            out |= self.b.related(c)
        return out

    # ───── H1: дата ─────
    def h1_dates(self):
        b = self.b
        cand_things, cand_rule = [], []
        for cid, c in self.C.items():
            if c["ctype"] in DATED_THINGS:
                g = self.date(cid)
                if g and g[1][2] == "год":
                    cand_things.append((cid, g[1][0], g[1][1]))
            if c["ctype"] == PERSON:
                g = self.date(cid, ("правление",))
                if g and g[1][2] == "год":
                    cand_rule.append((cid, g[1][0], g[1][1]))
        for cid, a, z in cand_things + cand_rule:
            c = self.C[cid]
            lab = c["label"]
            person = c["ctype"] == PERSON
            pool = cand_rule if person else cand_things
            what, info = self.date(cid, ("правление",)) if person else self.date(cid)
            if person:
                q, role = f"Укажите годы правления: {lab}.", "даты"
            elif c["ctype"] == "событие":
                q = f"Когда произошло событие «{lab}»?" if a == z else f"В какие годы происходило событие «{lab}»?"
                role = "даты"
            elif c["ctype"] in ("процесс", "период"):
                q, role = f"Каковы хронологические рамки: «{lab}»?", self.role("периоды", "даты")
            elif c["ctype"] == "документ":
                q, role = f"Когда был принят (создан) документ «{lab}»?", "даты"
            else:
                q, role = f"Когда был создан памятник культуры «{lab}»?", self.role("даты")
            right = fmt_span(a, z)
            single = a == z
            r = rng("H1" + cid)
            texts, seen = [], {right}
            same = [(o, x, y) for o, x, y in pool if o != cid and (x == y) == single and gap((x, y), (a, z)) >= GAP_DATE]
            for o, x, y in self.choose(r, same, 3, key=lambda t: abs((t[1] + t[2]) - (a + z))):
                t = fmt_span(x, y)
                if t not in seen:
                    seen.add(t); texts.append(t)
            synthetic = False
            for d in (7, -11, 15, -23, 31, -40, 52):
                if len(texts) >= 3:
                    break
                x, y = a + d, z + d
                if (a > 0 and x <= 0) or (a < 0 and y >= 0) or 0 in (x, y) or abs(x) > 2100 or abs(y) > 2100:
                    continue
                t = fmt_span(x, y)
                if t not in seen:
                    seen.add(t); texts.append(t); synthetic = True
            if len(texts) < 3:
                continue
            b.add("H1", slug(cid), [lab], role, q, [right] + texts[:3], [0], explanation=info[4],
                  basis={"knowledge": [info[3]], "dates": [a, z], "synthetic_distractors": synthetic})

    # ───── H2: событие по дате ─────
    def h2_by_date(self):
        b = self.b
        dated = [(cid, self.span(cid)) for cid, c in self.C.items() if c["ctype"] in EVENTS and self.span(cid)]
        for cid, (a, z) in dated:
            g = self.date(cid)
            if g[1][2] != "год":
                continue
            ex = self.excl(cid)
            pool = [o for o, s in dated if o not in ex and gap(s, (a, z)) >= 2]
            r = rng("H2" + cid)
            ds = self.choose(r, pool, 3, key=lambda o: (self.C[o].get("course") != self.C[cid].get("course"), abs(sum(self.span(o)) - (a + z))))
            if len(ds) < 3:
                continue
            q = f"Какое событие произошло в {fmt_year(a)}?" if a == z else f"Какое событие происходило в {fmt_span(a, z)}?"
            b.add("H2", slug(cid), [self.lab(cid)], "даты", q, [self.lab(cid)] + [self.lab(d) for d in ds], [0],
                  explanation=g[1][4], basis={"knowledge": [g[1][3]], "dates": [a, z]})

    # ───── H3: век, тысячелетие, часть века ─────
    def h3_century(self):
        b = self.b
        role_c = self.role("век", "даты")
        has_mil = b.role("тысячелетие")
        has_half = b.role("часть_века")
        for cid, c in sorted(self.C.items()):
            if c["ctype"] not in DATED_THINGS + (PERSON,):
                continue
            g = self.date(cid)
            if not g:
                continue
            what, (a, z, prec, kid, text) = g
            lab = c["label"]
            if c["ctype"] == "событие":
                tail = f"событие «{lab}»"
            elif c["ctype"] == "документ":
                tail = f"документ «{lab}»"
            elif c["ctype"] == "памятник_культуры":
                tail = f"памятник культуры «{lab}»"
            elif c["ctype"] == PERSON:
                tail = None
            else:
                tail = f"«{lab}»"
            if century(a) == century(z):
                cc = century(a)
                r = rng("H3" + cid)
                off = r.randrange(4)
                win = [step(cc, i - off) for i in range(4)]
                q = (f"В каком веке жил и действовал исторический деятель: {lab}?" if tail is None
                     else f"К какому веку относится {tail}?")
                note = " Года 00 завершают век: 1700 г. — последний год XVII в." if prec == "год" and (a % 100 == 0 or z % 100 == 0) else ""
                b.add("H3", slug(cid), [lab], role_c, q, [fmt_century(cc)] + [fmt_century(x) for x in win if x != cc], [0],
                      explanation=text + note, basis={"knowledge": [kid], "dates": [a, z], "century": cc})
            if has_mil and tail is not None and millennium(a) == millennium(z):
                m = millennium(a)
                r = rng("H3m" + cid)
                off = r.randrange(4)
                win = [step(m, i - off) for i in range(4)]
                b.add("H3m", slug(cid), [lab], "тысячелетие", f"К какому тысячелетию относится {tail}?",
                      [fmt_millennium(m)] + [fmt_millennium(x) for x in win if x != m], [0], explanation=text,
                      basis={"knowledge": [kid], "dates": [a, z], "millennium": m})
            if has_half and tail is not None and prec == "год" and a > 0 and self._half(a) == self._half(z) and self._half(a):
                h = self._half(a)
                r = rng("H3h" + cid)
                off = r.randrange(4)
                win = [self._half_step(h, i - off) for i in range(4)]
                b.add("H3h", slug(cid), [lab], "часть_века", f"К какой части века относится {tail}?",
                      [self._fmt_half(h)] + [self._fmt_half(x) for x in win if x != h], [0], explanation=text,
                      basis={"knowledge": [kid], "dates": [a, z]})

    @staticmethod
    def _half(y):
        if y % 100 in (0, 1, 50, 51):          # на границе половин: ответ спорный
            return None
        return (century(y), 1 if (y - 1) % 100 < 50 else 2)

    @staticmethod
    def _half_step(h, k):
        n = h[0] * 2 + (h[1] - 1) + k
        return (n // 2, n % 2 + 1)

    @staticmethod
    def _fmt_half(h):
        return f"{'первая' if h[1] == 1 else 'вторая'} половина {fmt_century(h[0])}"

    # ───── H4: последовательность ─────
    def h4_order(self):
        b = self.b
        items = []
        for cid, c in self.C.items():
            if c["ctype"] in ("событие", "процесс", "документ"):
                g = self.date(cid)
                if g and g[1][2] == "год":
                    items.append(cid)
        items.sort()
        if len(items) < 4:
            return
        by_sec = defaultdict(list)
        for cid in items:
            by_sec[self.C[cid]["section"]].append(cid)
        target = min(40, len(items) // 2)
        used, groups, made = Counter(), set(), 0
        for i in range(600):
            if made >= target:
                break
            r = rng(f"H4#{i}")
            secs = [s for s, xs in sorted(by_sec.items()) if len(xs) >= 4]
            pool = by_sec[r.choice(secs)] if secs and r.random() < 0.5 else items
            grp = sorted(r.sample(pool, 4), key=lambda x: self.span(x))
            sp = [self.span(x) for x in grp]
            if any(sp[j][1] + 2 > sp[j + 1][0] for j in range(3)):
                continue
            fs = frozenset(grp)
            if fs in groups or any(used[x] >= 2 for x in grp):
                continue
            groups.add(fs)
            for x in grp:
                used[x] += 1
            made += 1
            b.add("H4", b.nkey("H4", grp[0]), [self.lab(x) for x in grp], self.role("последовательность", "даты"),
                  "Расположите события в хронологической последовательности — от более раннего к более позднему.",
                  [self.lab(x) for x in grp], None, order=True,
                  explanation=" → ".join(f"{self.lab(x)} ({fmt_span(*self.span(x))})" for x in grp),
                  basis={"knowledge": [self.date(x)[1][3] for x in grp], "dates": [list(s) for s in sp]})

    # ───── H5: сколько лет прошло ─────
    def h5_years(self):
        b = self.b
        if not b.role("длительность"):
            return
        ys = []
        for cid, c in sorted(self.C.items()):
            if c["ctype"] in ("событие", "документ"):
                g = self.date(cid)
                if g and g[1][2] == "год" and g[1][0] == g[1][1]:
                    ys.append((cid, g[1][0]))
        pairs = [(p, q) for i, p in enumerate(ys) for q in ys[i + 1:] if p[1] != q[1]]
        r = rng("H5")
        r.shuffle(pairs)
        pairs.sort(key=lambda pq: not ((pq[0][1] < 0) != (pq[1][1] < 0)))     # сначала пары через рубеж эр
        made, used = 0, Counter()
        for (c1, y1), (c2, y2) in pairs:
            if made >= 12:
                break
            if used[c1] >= 2 or used[c2] >= 2:
                continue
            if y1 > y2:
                (c1, y1), (c2, y2) = (c2, y2), (c1, y1)
            n = years_between(y1, y2)
            cross = y1 < 0 < y2
            used[c1] += 1; used[c2] += 1; made += 1
            expl = f"От {fmt_year(y1)} до {fmt_year(y2)} прошло {n} лет."
            if cross:
                expl += (f" Года 0 нет: за 1 г. до н. э. сразу идёт 1 г. н. э., поэтому {-y1} + {y2} − 1 = {n}."
                         f" Ответ {n + 1} (без учёта этого) тоже засчитывается: так считают во многих школьных задачах.")
                check = {"type": "number", "value": n + 0.5, "rel_tol": round(0.5001 / (n + 0.5), 8), "unit": "лет",
                         "accept": [n, n + 1]}
            else:
                check = {"type": "number", "value": n, "rel_tol": 1e-6, "unit": "лет"}
            b.add("H5", f"{slug(c1)}.{slug(c2)}", [self.lab(c1), self.lab(c2)], "длительность",
                  f"Сколько лет прошло от события «{self.lab(c1)}» ({fmt_year(y1)}) до события «{self.lab(c2)}» ({fmt_year(y2)})?",
                  fmt="число", answer=n, check=check, explanation=expl,
                  basis={"knowledge": [self.date(c1)[1][3], self.date(c2)[1][3]], "dates": [y1, y2]})

    # ───── H6: современники ─────
    def h6_contemporaries(self):
        b = self.b
        if not b.role("современники"):
            return
        persons = [cid for cid, c in self.C.items() if c["ctype"] == PERSON]
        for cid, c in sorted(self.C.items()):
            if c["ctype"] not in EVENT_LIKE or not self.span(cid):
                continue
            sp = self.span(cid)
            rights = sorted(self.parts_of.get(cid, set()) & set(persons))
            why = "участник"
            if not rights:
                rights = sorted(p for p in persons if self.date(p, ("жизнь",)) and self.date(p, ("жизнь",))[1][0] <= sp[0]
                                and self.date(p, ("жизнь",))[1][1] >= sp[1])
                why = "годы жизни"
            if not rights:
                continue
            r = rng("H6" + cid)
            right = r.choice(rights)
            ex = self.excl(cid, right) | self.parts_of.get(cid, set())
            pool = [p for p in persons if p not in ex and self.wide(p) and gap(self.wide(p), sp) >= GAP_TIME]
            ds = self.choose(r, pool, 3, key=lambda p: gap(self.wide(p), sp))
            if len(ds) < 3:
                continue
            g = self.date(cid)
            expl = (f"{self.lab(right)} — участник: «{c['label']}» ({fmt_span(*sp)})." if why == "участник"
                    else f"Годы жизни: {self.lab(right)} — {fmt_span(*self.date(right, ('жизнь',))[1][:2])}; «{c['label']}» — {fmt_span(*sp)}")
            what = f"события «{c['label']}»" if c["ctype"] == "событие" else f"«{c['label']}» ({fmt_span(*sp)})"
            b.add("H6", slug(cid), [c["label"], self.lab(right)], "современники",
                  f"Кто из перечисленных исторических деятелей был современником {what}?",
                  [self.lab(right)] + [self.lab(d) for d in ds], [0], explanation=expl,
                  basis={"knowledge": [g[1][3]], "edge": ["participant_of", right, cid] if why == "участник" else None})

    # ───── H7: синхронность ─────
    def h7_sync(self):
        b = self.b
        if not b.role("синхронность"):
            return
        ev = [cid for cid, c in self.C.items() if c["ctype"] in ("событие", "процесс") and c.get("course")
              and self.date(cid) and self.date(cid)[1][2] == "год"]
        ru = [x for x in ev if self.C[x]["course"] == "россия"]
        wo = [x for x in ev if self.C[x]["course"] == "всеобщая"]
        for src, other, name in ((ru, wo, "всеобщей истории"), (wo, ru, "истории России")):
            for cid in sorted(src):
                sp = self.span(cid)
                rights = sorted(o for o in other if gap(self.span(o), sp) <= 1)
                if not rights:
                    continue
                r = rng("H7" + cid)
                right = r.choice(rights)
                pool = [o for o in other if gap(self.span(o), sp) >= GAP_SYNC and gap(self.span(o), self.span(right)) >= GAP_SYNC]
                ds = self.choose(r, pool, 3, key=lambda o: gap(self.span(o), sp))
                if len(ds) < 3:
                    continue
                b.add("H7", slug(cid), [self.lab(cid), self.lab(right)], "синхронность",
                      f"Какое событие {name} произошло в те же годы, что и «{self.lab(cid)}» ({fmt_span(*sp)})?",
                      [self.lab(right)] + [self.lab(d) for d in ds], [0],
                      explanation=f"{self.lab(right)} — {fmt_span(*self.span(right))}",
                      basis={"knowledge": [self.date(cid)[1][3], self.date(right)[1][3]]})

    # ───── U1/U2: участники ─────
    def u_participants(self):
        b = self.b
        persons = [cid for cid, c in self.C.items() if c["ctype"] == PERSON]
        per_t = Counter()
        for e in sorted(b.edges["participant_of"], key=lambda e: (e["target"], e["source"])):
            p, t = e["source"], e["target"]
            if self.C[t]["ctype"] not in EVENT_LIKE + ("государство_народ",) or per_t[t] >= 2:
                continue
            ex = self.excl(p, t) | self.parts_of.get(t, set())
            for x in self.b.anc.get(t, set()) | self.b.desc.get(t, set()):
                ex |= self.parts_of.get(x, set())
            pool = [d for d in persons if d not in ex and self.safe(t, d)]
            r = rng("U1" + p + t)
            ds = self.choose(r, pool, 3, key=lambda d: (self.C[d]["section"] != self.C[t]["section"],))
            if len(ds) < 3:
                continue
            tl = self.lab(t)
            ct = self.C[t]["ctype"]
            q = (f"Кто из перечисленных исторических деятелей был участником события «{tl}»?" if ct == "событие"
                 else f"Чья деятельность связана с «{tl}»?")
            per_t[t] += 1
            b.add("U1", f"{slug(t)}.{slug(p)}", [tl, self.lab(p)], "участники" if ct == "событие" else self.role("личности", "участники"), q,
                  [self.lab(p)] + [self.lab(d) for d in ds], [0], explanation=e.get("why", ""), basis={"edge": ["participant_of", p, t]})
        targets_all = [cid for cid, c in self.C.items() if c["ctype"] in EVENT_LIKE]
        for p in sorted(self.part):
            ts = sorted(t for t in self.part[p] if self.C[t]["ctype"] in EVENT_LIKE)
            if not ts:
                continue
            r = rng("U2" + p)
            t = r.choice(ts)
            ex = self.excl(p, *ts)
            for x in ts:
                ex |= self.b.anc.get(x, set()) | self.b.desc.get(x, set())
            pool = [d for d in targets_all if d not in ex and self.safe(p, d) and all(self.safe(x, d, 10) for x in ts)]
            ds = self.choose(r, pool, 3, key=lambda d: (self.C[d]["ctype"] != self.C[t]["ctype"],))
            if len(ds) < 3:
                continue
            why = next((e.get("why", "") for e in b.edges["participant_of"] if e["source"] == p and e["target"] == t), "")
            b.add("U2", slug(p), [self.lab(p), self.lab(t)], self.role("личности", "участники"),
                  f"С каким событием (процессом) связана деятельность исторического деятеля «{self.lab(p)}»?",
                  [self.lab(t)] + [self.lab(d) for d in ds], [0], explanation=why, basis={"edge": ["participant_of", p, t]})

    # ───── U3/U4: места, документы, памятники ─────
    def _links(self, a_types, b_types):
        out = defaultdict(list)
        for t in ("prerequisites_for", "part_of", "participant_of"):
            for e in self.b.edges.get(t, []):
                s, d = e["source"], e["target"]
                for x, y in ((s, d), (d, s)):
                    if self.C[x]["ctype"] in a_types and self.C[y]["ctype"] in b_types:
                        out[x].append((y, e))
        return out

    def u_places_docs(self):
        b = self.b
        places = [cid for cid, c in self.C.items() if c["ctype"] == "место"]
        for ev, lst in sorted(self._links(EVENTS, ("место",)).items()):
            m, e = sorted(lst, key=lambda x: x[0])[0]
            ex = self.excl(ev, m) | {y for y, _ in lst}
            r = rng("U3" + ev)
            ds = self.choose(r, [p for p in places if p not in ex and self.C[p]["element"] != self.C[ev]["element"]], 3)
            if len(ds) < 3:
                continue
            b.add("U3", slug(ev), [self.lab(ev), self.lab(m)], "участники", f"С каким местом связано событие «{self.lab(ev)}»?",
                  [self.lab(m)] + [self.lab(d) for d in ds], [0], explanation=e.get("why", ""), basis={"edge": [e["type"], e["source"], e["target"]]})
        order = {PERSON: 0, "событие": 1, "государство_народ": 2, "процесс": 3}
        for x, lst in sorted(self._links(("документ", "памятник_культуры"), tuple(order)).items()):
            y, e = sorted(lst, key=lambda t: (order[self.C[t[0]]["ctype"]], t[0]))[0]
            cy, cx = self.C[y]["ctype"], self.C[x]["ctype"]
            doc = cx == "документ"
            thing = f"документ «{self.lab(x)}»" if doc else f"памятник культуры «{self.lab(x)}»"
            q = {PERSON: f"С каким историческим деятелем связан {thing}?",
                 "событие": f"С каким событием связан {thing}?", "процесс": f"С каким процессом (явлением) связан {thing}?",
                 "государство_народ": (f"В каком государстве был создан документ «{self.lab(x)}»?" if doc
                                       else f"К культуре какого государства (народа) относится памятник «{self.lab(x)}»?")}[cy]
            ex = self.excl(x, y) | {t for t, _ in lst}
            pool = [d for d, c in self.C.items() if c["ctype"] == cy and d not in ex and self.safe(y, d)]
            r = rng("U4" + x)
            ds = self.choose(r, pool, 3)
            if len(ds) < 3:
                continue
            b.add("U4", slug(x), [self.lab(x), self.lab(y)], self.role("документы", "источники") if doc else self.role("культура", "описывать"),
                  q, [self.lab(y)] + [self.lab(d) for d in ds], [0], explanation=e.get("why", ""),
                  basis={"edge": [e["type"], e["source"], e["target"]]})

    # ───── Y1: группировка ─────
    def y_group(self):
        b = self.b
        ch = defaultdict(list)
        for e in b.edges["part_of"]:
            if self.C[e["source"]]["ctype"] in EVENT_LIKE and self.C[e["target"]]["ctype"] in EVENT_LIKE + ("государство_народ",):
                ch[e["target"]].append(e["source"])
        for pa, kids in sorted(ch.items()):
            if len(kids) < 2:
                continue
            r = rng("Y1" + pa)
            kids = sorted(kids)
            r.shuffle(kids)
            right = kids[:3]
            ex = self.excl(pa, *kids) | self.b.desc.get(pa, set())
            types = {self.C[k]["ctype"] for k in right}
            pool = [d for d, c in self.C.items() if c["ctype"] in types and d not in ex and self.safe(pa, d, 10)]
            ds = self.choose(r, pool, 2, key=lambda d: (self.C[d].get("course") != self.C[pa].get("course"),))
            if len(ds) < 2:
                continue
            b.add("Y1", slug(pa), [self.lab(pa)] + [self.lab(k) for k in right], self.role("систематизировать", "описывать"),
                  f"Какие из перечисленных событий (явлений) относятся к «{self.lab(pa)}»? Выберите все верные ответы.",
                  [self.lab(k) for k in right] + [self.lab(d) for d in ds], list(range(len(right))),
                  explanation="Части целого в графе: " + "; ".join(f"{self.lab(k)} → {self.lab(pa)}" for k in right),
                  basis={"edges": [["part_of", k, pa] for k in right]})

    # ───── D: итоги, причины, деятельность, значение ─────
    def d_essence(self):
        b = self.b
        spec = {
            "итог": ("D1", lambda c: f"Каким был итог (результат) события «{c['label']}»?" if c["ctype"] == "событие"
                     else f"Каковы итоги (результаты): «{c['label']}»?", self.role("участники", "объяснять")),
            "причина": ("D2", lambda c: f"Что стало причиной (предпосылкой) события «{c['label']}»?" if c["ctype"] == "событие"
                        else f"Что стало причиной (предпосылкой): «{c['label']}»?", self.role("объяснять")),
            "деятельность": ("D3", lambda c: f"Что относится к деятельности исторического деятеля «{c['label']}»?", self.role("личности", "участники")),
            "значение": ("D5", lambda c: f"Каково историческое значение: «{c['label']}»?", self.role("значение", "объяснять")),
            "черты": ("D5", lambda c: f"Какие черты характерны для «{c['label']}»?", self.role("описывать")),
            "описание": ("D5", lambda c: f"Какое описание относится к памятнику культуры «{c['label']}»?" if c["ctype"] == "памятник_культуры"
                         else f"Какое описание относится к «{c['label']}»?", self.role("культура", "описывать")),
            "содержание": ("D5", lambda c: f"Что устанавливал (о чём говорил) документ «{c['label']}»?" if c["ctype"] == "документ"
                           else f"Каково содержание: «{c['label']}»?", self.role("документы", "источники")),
        }
        types = {"итог": EVENT_LIKE, "причина": EVENT_LIKE, "деятельность": (PERSON,), "описание": ("памятник_культуры",),
                 "содержание": ("документ",)}
        for kind, (fam, qf, role) in spec.items():
            pool_all = {cid: k for cid, k in self.ess.get(kind, {}).items()
                        if kind not in types or self.C[cid]["ctype"] in types[kind]}
            for cid, k in sorted(pool_all.items()):
                c = self.C[cid]
                names = [c["label"]] + c.get("aliases", [])
                ex = self.excl(cid)
                pool = [o for o in pool_all if o not in ex and self.C[o]["element"] != c["element"]
                        and not names_concept(pool_all[o]["essence"], names)]
                r = rng(fam + kind + cid)
                ds = self.choose(r, pool, 3, key=lambda o: (self.C[o]["ctype"] != c["ctype"], self.C[o]["section"] != c["section"]))
                texts = list(dict.fromkeys([k["essence"]] + [pool_all[d]["essence"] for d in ds]))
                if len(texts) < 4:
                    continue
                key = slug(cid) if fam != "D5" else f"{kind}.{slug(cid)}"
                b.add(fam, key, [c["label"]], role, qf(c), texts, [0], explanation=k["text"],
                      basis={"knowledge": [k["id"]] + [pool_all[d]["id"] for d in ds]})
        acts = {cid: k for cid, k in self.ess.get("деятельность", {}).items() if self.C[cid]["ctype"] == PERSON}
        persons = [cid for cid, c in self.C.items() if c["ctype"] == PERSON]
        for cid, k in sorted(acts.items()):
            ex = self.excl(cid)
            pool = [p for p in persons if p not in ex and self.safe(cid, p, 20)]
            r = rng("D4" + cid)
            ds = self.choose(r, pool, 3, key=lambda p: (self.C[p].get("course") != self.C[cid].get("course"), self.C[p]["section"] == self.C[cid]["section"]))
            if len(ds) < 3:
                continue
            b.add("D4", slug(cid), [self.lab(cid)], self.role("личности", "участники"),
                  f"О каком историческом деятеле идёт речь: «{k['essence']}»?", [self.lab(cid)] + [self.lab(d) for d in ds], [0],
                  explanation=k["text"], basis={"knowledge": [k["id"]]})

    def build(self):
        self.h1_dates(); self.h2_by_date(); self.h3_century(); self.h4_order(); self.h5_years(); self.h6_contemporaries(); self.h7_sync()
        self.u_participants(); self.u_places_docs(); self.y_group()
        self.d_essence()
