#!/usr/bin/env python3
"""Этапы «задания» и «итог»: сборка бенчмарка среза из графа, знаний и решений LLM — общая часть для всех предметов.

Вход (папка прогона): 3_знания/<срез>/срез.json, знания.jsonl, формулы.json; решения/задания_llm.json, решения/дистракторы.json
(для «итог»); ручные_данные/<предмет>/<срез>/справочные_данные.json, шаблоны_расчёт.py, близкие_пары.json, близкие_формулы.json (необязательные).
Выход: 4_бенчмарк/<срез>/задания.jsonl, сводка.json, задания.md, просмотр.html.

Здесь — общие семейства (вид и часть по графу, определения, ошибочные и верные утверждения, ситуации, развёрнутые ответы,
прочие задания модели, структура программы) и сборка набора. Семейства одного предмета — в модуле предмета:
физика/phys_tasks.py (опыты, приборы, формулы, расчёт, цепочки), история/hist_tasks.py (хронология, участники, итоги…);
какой модуль и какие семейства — задаёт профиль предмета (bench_common.PROFILE).

Семейства привязаны к ролям умений ФОП («понятия», «описывать», «решать» …), код умения берётся у класса среза по тексту:
у 7 и 8 классов физики нумерация умений разная. Варианты ответов перемешиваются детерминированно (зерно — id задания).
«итог» (аргумент --итог) — то же плюс пометки проверки неверных вариантов моделью и автоматическая проверка набора.
"""
import importlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

from bench_common import (C, LLM_STATUS, OUT_OF_SCOPE, PROFILE, SLICE, bn, slice_title, dec, jl, jload, kn, load_slice_full, manual,
                          resolve_role, rng, slug, stems, write_jsonl)

HERE = os.path.dirname(os.path.abspath(__file__))
T = importlib.import_module(PROFILE.TASKS_MODULE)      # семейства предмета: phys_tasks / hist_tasks
FAMILY = PROFILE.FAMILY                                # код семейства → (название, дорожка); порядок — порядок в задания.md
SITUATION_Q = PROFILE.SITUATION_Q                      # вопрос к описанию ситуации, если модель не закончила его вопросом
TRUE_KINDS = PROFILE.TRUE_KINDS                        # виды знаний, из которых берутся верные утверждения для K5 и K8


def num(code):
    return [int(p) for p in re.findall(r"\d+", code or "")]


class Builder(T.Families):
    """Сборщик набора; методы семейств предмета подмешаны из T.Families."""

    def __init__(self):
        self.sl = load_slice_full()
        s = self.sl
        self.srez, self.C, self.L, self.req = s.s, s.C, s.L, s.req
        self.know = jl(kn("знания.jsonl"))
        self.forms = jload(kn("формулы.json")) if os.path.exists(kn("формулы.json")) else {"формулы": [], "величины": {}, "единицы": {}, "обозначения": {}, "единицы_через_основные": []}
        self.items = jload(dec("задания_llm.json")) if os.path.exists(dec("задания_llm.json")) else {}
        self.ref = jload(manual("справочные_данные.json")) if manual("справочные_данные.json") else None
        self.tasks, self.problems, self.skipped = [], [], []
        self.edges, self.adj, self.anc, self.desc = defaultdict(list, s.edges), s.adj, s.anc, s.desc
        self.pre, self.pre_all, self.succ_all = defaultdict(set, s.pre), s.pre_all, s.succ_all
        self.related = s.related
        self.counter = Counter()
        self.seen_q = set()
        # роль умения → код по разделу понятия (у СОО «описывать» — три умения: механика, тепловые, электрические явления)
        self.role_by_sec = jload(manual("умения.json")) if manual("умения.json") else {}

    def lab(self, cid):
        return self.C[cid]["label"]

    def role(self, r):
        return resolve_role(self.srez["requirements"], r)

    def nkey(self, fam, cid):
        """Ключ задания по понятию и порядковому номеру: не зависит от порядка разделов и записей."""
        self.counter[(fam, cid)] += 1
        return f"{slug(cid)}.{self.counter[(fam, cid)]:02d}"

    # ───── добавление задания ─────
    def add(self, fam, key, concepts, req, question, options=None, correct=None, *, fmt=None, answer=None, check=None,
            explanation="", steps=1, basis=None, status="авто", order=False):
        tid = f"T.{SLICE}.{fam}.{key}"
        cids = [self.L[c]["id"] if c in self.L else c for c in concepts]
        for c in cids:
            if c not in self.C:
                self.problems.append(f"{tid}: неизвестное понятие {c}")
                return
        code = self.role(req)
        if req in self.role_by_sec:
            code = self.role_by_sec[req].get(self.C[cids[0]]["section"].split(".")[0], code)
        if code is None:
            self.problems.append(f"{tid}: у класса нет умения «{req}»")
        t = {"id": tid, "family": fam, "family_name": FAMILY[fam][0], "track": FAMILY[fam][1],
             "requirement": code, "requirement_text": self.req[code]["text"] if code in self.req else None,
             "concepts": cids, "concept_labels": [self.lab(c) for c in cids],
             "element": self.C[cids[0]]["element"], "section": self.C[cids[0]]["section"],
             "exam_items": sorted({x for c in cids for x in self.C[c].get("exam_items", [])}),
             "question": question, "steps": steps, "status": status, "basis": basis or {}}
        if PROFILE.SKIP_DUPLICATES:
            # задание с повторяющимися вариантами или тем же вопросом не берётся (у физики — запись в проблемы, как раньше)
            if options is not None and len(set(options)) != len(list(options)):
                self.skipped.append(f"{tid}: повторяющиеся варианты — пропущено")
                return
            qk = (question, tuple(sorted(options)) if options is not None else None)
            if qk in self.seen_q:
                self.skipped.append(f"{tid}: такое задание уже есть — пропущено")
                return
            self.seen_q.add(qk)
        if options is not None:
            opts = list(options)
            if not order and any(len(opts[i]) > 6 and opts[i].lower() in question.lower() for i in correct):
                self.skipped.append(f"{tid}: ответ содержится в вопросе — пропущено")
                return
            r = rng(tid)
            if order:
                perm = list(range(len(opts)))
                while perm == sorted(perm):
                    r.shuffle(perm)
                keys = [str(i + 1) for i in range(len(opts))]
                t["format"] = "порядок"
                t["options"] = [{"key": k, "text": opts[i]} for k, i in zip(keys, perm)]
                ans = "".join(str(perm.index(i) + 1) for i in range(len(opts)))
                t["answer"], t["check"] = ans, {"type": "sequence", "value": ans}
            else:
                idx = list(range(len(opts)))
                r.shuffle(idx)
                t["options"] = [{"key": str(k + 1), "text": opts[i]} for k, i in enumerate(idx)]
                corr = "".join(sorted(str(idx.index(i) + 1) for i in correct))
                t["format"] = "один_ответ" if len(correct) == 1 else "несколько_ответов"
                t["answer"], t["check"] = corr, {"type": "choice", "value": corr}
            if len(set(opts)) != len(opts):
                self.problems.append(f"{tid}: повторяющиеся варианты")
        else:
            t["format"], t["answer"], t["check"] = fmt, answer, check
        t["explanation"] = explanation
        self.tasks.append(t)

    def pick(self, r, pool, k, exclude=(), avoid_stems=()):
        pool = [p for p in dict.fromkeys(pool) if p not in exclude]
        if avoid_stems:
            st = set().union(*[stems(self.lab(a)) for a in avoid_stems])
            pool = [p for p in pool if not (stems(self.lab(p)) & st)]
        r.shuffle(pool)
        return pool[:k]

    # ───── G: из связей графа ─────
    def g_isa(self):
        parents = defaultdict(list)
        for e in self.edges["is_a"]:
            parents[e["target"]].append(e["source"])
        all_parents = list(parents)
        n_par = Counter(e["source"] for e in self.edges["is_a"])   # у понятия два рода (гамма-излучение) — в id задания добавляется род
        for e in self.edges["is_a"]:
            ch, pa = e["source"], e["target"]
            r = rng("G1" + ch + pa)
            pool = [p for p in all_parents if self.C[p]["ctype"] == self.C[pa]["ctype"]] + [p for p in all_parents if p != pa]
            ds = self.pick(r, pool, 3, exclude=self.related(ch) | {pa}, avoid_stems=[ch, pa])
            if len(ds) < 3:
                self.problems.append(f"G1 {self.lab(ch)}: мало неверных вариантов"); continue
            self.add("G1", slug(ch) if n_par[ch] == 1 else f"{slug(ch)}.{slug(pa)}", [self.lab(ch), self.lab(pa)], "понятия", f"«{self.lab(ch)}» — это частный случай (вид) какого понятия?",
                     [self.lab(pa)] + [self.lab(d) for d in ds], [0], explanation=e.get("why", ""), basis={"edge": ["is_a", ch, pa]})
        for pa, chs in parents.items():
            if len(chs) < 2:
                continue
            r = rng("G1m" + pa)
            chs = sorted(chs)
            r.shuffle(chs)
            right = chs[:3]
            ctypes = {self.C[c]["ctype"] for c in right}
            if ctypes == {"опыт"}:
                ctypes = {"прибор", "величина"}  # другие опыты тоже содержат измерения — неоднозначно
            pool = [c for c in self.C if self.C[c]["ctype"] in ctypes]
            ex = self.related(pa) | set(chs)
            for c in chs:
                ex |= self.related(c)
            ds = self.pick(r, pool, 2, exclude=ex, avoid_stems=[pa] + right)
            if len(ds) < 2:
                self.problems.append(f"G1m {self.lab(pa)}: мало неверных вариантов"); continue
            self.add("G1m", slug(pa), [self.lab(pa)] + [self.lab(c) for c in right], "понятия",
                     f"Какие из перечисленных понятий являются видами (частными случаями) понятия «{self.lab(pa)}»? Выберите все верные ответы.",
                     [self.lab(c) for c in right] + [self.lab(d) for d in ds], list(range(len(right))),
                     explanation="Связи «вид» в графе: " + "; ".join(f"{self.lab(c)} → {self.lab(pa)}" for c in right),
                     basis={"edges": [["is_a", c, pa] for c in right]})

    def g_part(self):
        tg = sorted({e["target"] for e in self.edges["part_of"]})
        n_par = Counter(e["source"] for e in self.edges["part_of"])   # событие бывает частью двух целых — к id добавляется целое (профиль)
        for e in self.edges["part_of"]:
            ch, pa = e["source"], e["target"]
            r = rng("G2" + ch)
            pool = [c for c in self.C if self.C[c]["ctype"] in ("понятие", "величина", "закон", "термин", "процесс")]
            ds = self.pick(r, [p for p in tg if p != pa] + pool, 3, exclude=self.related(ch) | {pa}, avoid_stems=[ch, pa])
            if len(ds) < 3:
                self.problems.append(f"G2 {self.lab(ch)}: мало неверных вариантов"); continue
            key = f"{slug(ch)}.{slug(pa)}" if PROFILE.PART_KEY_WITH_PARENT and n_par[ch] > 1 else slug(ch)
            self.add("G2", key, [self.lab(ch), self.lab(pa)], "понятия", f"Частью (составной частью, этапом) чего является «{self.lab(ch)}»?",
                     [self.lab(pa)] + [self.lab(d) for d in ds], [0], explanation=e.get("why", ""), basis={"edge": ["part_of", ch, pa]})

    # ───── K: из знаний ─────
    def _defs(self):
        out = {}
        for k in self.know:
            if k["kind"] != "определение" or k["status"] == "без источника":
                continue
            c = self.C[k["concept_id"]]
            names = [c["label"]] + c.get("aliases", [])
            m = re.match(r"^(.+?)\s+—\s+(.+)$", k["text"])
            if not m:
                continue
            head, body = m.group(1), m.group(2).rstrip(".")
            if ":" in head or len(head) > max(len(n) for n in names) + 12 or not any(head.lower().startswith(n.lower()[:6]) for n in names):
                continue
            if any(n.lower() in body.lower() for n in names):
                continue
            out.setdefault(c["id"], (k, body))
        return out

    def k_defs(self):
        defs = self._defs()
        ids = list(defs)

        def hard_first(r, cid, pool):
            pool = [x for x in dict.fromkeys(pool) if x not in self.related(cid)]
            r.shuffle(pool)
            sib = {x for p in self.anc[cid] for x in self.desc.get(p, ())}
            st = stems(self.lab(cid))
            return sorted(pool, key=lambda x: (x not in sib, not (stems(self.lab(x)) & st)))
        for cid, (k, body) in defs.items():
            c = self.C[cid]
            ds = hard_first(rng("K1" + cid), cid, [x for x in ids if self.C[x]["ctype"] == c["ctype"]] + ids)[:3]
            if len(ds) == 3:
                self.add("K1", slug(cid), [c["label"]], "понятия", f"Какое определение соответствует понятию «{c['label']}»?",
                         [body] + [defs[d][1] for d in ds], [0], explanation=k["text"], basis={"knowledge": [k["id"]]})
            ds = hard_first(rng("K2" + cid), cid, [x for x in self.C if self.C[x]["ctype"] == c["ctype"]])[:3]
            if len(ds) == 3:
                self.add("K2", slug(cid), [c["label"]], "понятия", f"Как называется: «{body[0].upper() + body[1:]}»?",
                         [c["label"]] + [self.lab(d) for d in ds], [0], explanation=k["text"], basis={"knowledge": [k["id"]]})
        done = set()
        for k in self.know:
            if k["kind"] == "определение" and k["status"] != "без источника" and k["concept_id"] not in done:
                c = self.C[k["concept_id"]]
                done.add(c["id"])
                self.add("K3", slug(c["id"]), [c["label"]], "понятия", f"Объясните своими словами, что такое «{c['label']}» (дайте определение).",
                         fmt="развёрнутый", answer=k["text"],
                         check={"type": "rubric", "reference": k["text"], "max": 1,
                                "criteria": "1 балл — ответ передаёт все существенные признаки эталонного определения; 0 — признак пропущен или искажён."},
                         explanation=k["text"], basis={"knowledge": [k["id"]]}, status="авто, проверка по эталону")

    def _sections(self):
        return sorted(self.items, key=num)

    def _true_pool(self, section):
        return [k for k in self.know if k["kind"] in TRUE_KINDS and k["status"] == "подтверждено"
                and self.C[k["concept_id"]]["section"] == section and len(k["text"]) <= 230
                and "заблужд" not in k["text"]]

    def k_statements(self):
        i = 0
        for sec in self._sections():
            items = []
            for x in self.items[sec].get("неверные", []):
                if x["понятие"] not in self.L:
                    self.problems.append(f"неверные: нет понятия {x['понятие']}")
                else:
                    items.append((i, x["понятие"], x["утверждение"], x["почему"]))
                i += 1
            trues = self._true_pool(sec)
            for j, lab_, text, why in items:
                r = rng(f"K8{j}")
                cid = self.L[lab_]["id"]
                cand = [k for k in trues if k["concept_id"] not in self.related(cid)]
                r.shuffle(cand)
                picked, used = [], set()
                for k in cand:
                    if k["concept_id"] not in used:
                        picked.append(k); used.add(k["concept_id"])
                    if len(picked) == 3:
                        break
                if len(picked) < 3:
                    self.problems.append(f"K8 {lab_}: мало верных утверждений"); continue
                self.add("K8", self.nkey("K8", cid), [lab_] + [self.C[k["concept_id"]]["label"] for k in picked], "источники",
                         "Какое из утверждений содержит ошибку?", [text] + [k["text"] for k in picked], [0],
                         explanation=f"Неверно: «{text}» — {why}.", basis={"false": text, "knowledge": [k["id"] for k in picked]}, status=LLM_STATUS)
            r = rng(f"K5{sec}")
            items2 = items[:]
            r.shuffle(items2)
            for g0 in range(0, len(items2) - 2, 3):
                grp = items2[g0:g0 + 3]
                ex = set()
                for _, lab_, _, _ in grp:
                    ex |= self.related(self.L[lab_]["id"])
                cand = [k for k in trues if k["concept_id"] not in ex]
                r.shuffle(cand)
                picked, used = [], set()
                for k in cand:
                    if k["concept_id"] not in used:
                        picked.append(k); used.add(k["concept_id"])
                    if len(picked) == 2:
                        break
                if len(picked) < 2:
                    continue
                self.add("K5", f"{sec}.{g0 // 3 + 1:02d}", [self.C[k["concept_id"]]["label"] for k in picked] + [g[1] for g in grp], "описывать",
                         "Выберите два верных утверждения.", [k["text"] for k in picked] + [g[2] for g in grp], [0, 1],
                         explanation="Неверные: " + "; ".join(f"«{g[2]}» — {g[3]}" for g in grp) + ".",
                         basis={"knowledge": [k["id"] for k in picked], "false": [g[2] for g in grp]}, status=LLM_STATUS, steps=2)

    def k_situations(self):
        for sec in self._sections():
            for x in self.items[sec].get("ситуации", []):
                right, ds = x["ответ"], list(x["неверные"])
                bad = [y for y in [right] + ds if y not in self.L]
                if bad:
                    self.problems.append(f"ситуации: нет понятий {bad}"); continue
                sit = x["ситуация"]
                q = sit if sit.endswith("?") else f"{sit} {SITUATION_Q}"
                cid = self.L[right]["id"]
                self.add("K6", self.nkey("K6", cid), [right], x["умение"], q, [right] + ds, [0], status=LLM_STATUS,
                         basis={"knowledge": [k["id"] for k in self.know if k["concept_id"] == cid][:3]})

    def k_explain(self):
        for sec in self._sections():
            for x in self.items[sec].get("объяснения", []):
                cs = x["понятия"]
                bad = [y for y in cs if y not in self.L]
                if bad:
                    self.problems.append(f"объяснения: нет понятий {bad}"); continue
                ids = {self.L[c]["id"] for c in cs}
                role = x["умение"] if PROFILE.EXPLAIN_ROLE_FROM_ITEM and x.get("умение") and self.role(x["умение"]) else "объяснять"
                self.add("K7", self.nkey("K7", self.L[cs[0]]["id"]), cs, role, x["вопрос"], fmt="развёрнутый", answer=x["эталон"],
                         check={"type": "rubric", "reference": x["эталон"], "key_points": x["пункты"], "max": len(x["пункты"]),
                                "criteria": "по 1 баллу за каждый ключевой пункт, верно выраженный в ответе; рассуждение без опоры на закон или свойство — 0."},
                         explanation=x["эталон"], steps=x.get("шагов", 1), status=LLM_STATUS,
                         basis={"knowledge": [k["id"] for k in self.know if k["concept_id"] in ids][:4]})

    def k_other(self):
        for sec in self._sections():
            for x in self.items[sec].get("прочие", []):
                cs = x["понятия"]
                if any(c not in self.L for c in cs):
                    self.problems.append(f"прочие: нет понятий {cs}"); continue
                self.add("P", self.nkey("P", self.L[cs[0]]["id"]), cs, x["умение"], x["вопрос"], x["варианты"], x["верные"],
                         explanation=x.get("пояснение", ""), status=LLM_STATUS)

    # ───── S: структура программы ─────
    def s_structure(self):
        for c, cc in self.C.items():
            right, wrong = sorted(self.pre[c]), sorted(self.succ_all[c])
            if not right or not wrong or len(right[:3]) + len(wrong[:2]) < 3:
                continue
            r = rng("S1" + c)
            r.shuffle(right); r.shuffle(wrong)
            right, wrong = right[:3], wrong[:2]
            self.add("S1", slug(c), [cc["label"]], "понятия",
                     f"Какие из перечисленных понятий нужно знать заранее, чтобы разобраться в понятии «{cc['label']}»? Выберите все верные ответы.",
                     [self.lab(x) for x in right] + [self.lab(x) for x in wrong], list(range(len(right))),
                     explanation="Нужно знать до: " + ", ".join(self.lab(x) for x in right) + ". Опираются на это понятие (изучаются после): " + ", ".join(self.lab(x) for x in wrong) + ".",
                     basis={"prerequisites": right, "dependents": wrong}, status="авто (структура)")
        red = defaultdict(set)
        for t, ss in self.pre.items():
            for s in ss:
                if not any(s in self.pre_all[o] for o in ss if o != s):
                    red[s].add(t)
        chains = []

        def dfs(path):
            if len(path) == 4:
                chains.append(list(path)); return
            for n_ in sorted(red[path[-1]]):
                dfs(path + [n_])
        for s in sorted(self.C):
            if not self.pre[s]:
                dfs([s])
        rng("S2").shuffle(chains)
        used_end, out = set(), []
        for ch in chains:
            if ch[-1] not in used_end:
                used_end.add(ch[-1]); out.append(ch)
        for ch in out[:40]:
            self.add("S2", slug(ch[-1]), [self.lab(x) for x in ch[::-1]], "понятия",
                     "Расположите понятия в таком порядке, чтобы каждое следующее опиралось на предыдущее (от основы к тому, что на неё опирается).",
                     [self.lab(x) for x in ch], None, order=True, explanation=" → ".join(self.lab(x) for x in ch), basis={"chain": ch}, status="авто (структура)")

    # ───── пометки проверки неверных вариантов ─────
    def apply_flags(self):
        p = dec("дистракторы.json")
        flags = jload(p) if os.path.exists(p) else {}
        n = 0
        for t in self.tasks:
            f = flags.get(t["id"])
            if f and (f.get("спорные") or f.get("ключ_неверен")):
                t["distractor_check"] = f
                t["status"] = t["status"] + " · варианты под вопросом"
                n += 1
        return n, bool(flags)

    def weights(self):
        for track in ("содержание", "структура"):
            ts = [t for t in self.tasks if t["track"] == track]
            n = Counter(t["concepts"][0] for t in ts)
            tot_w = sum(self.C[c]["weight_pct"] for c in n)
            for t in ts:
                c = t["concepts"][0]
                t["concept_weight_pct"] = self.C[c]["weight_pct"]
                t["weight"] = round(self.C[c]["weight_pct"] / tot_w / n[c], 8) if tot_w else 0

    def summary(self):
        S = {"срез": SLICE, "всего": len(self.tasks), "по_дорожкам": Counter(t["track"] for t in self.tasks),
             "по_семействам": {f"{k} {FAMILY[k][0]}": v for k, v in sorted(Counter(t["family"] for t in self.tasks).items())},
             "по_умениям": dict(sorted(Counter(t["requirement"] or "?" for t in self.tasks).items(), key=lambda x: num(x[0]))),
             "по_разделам": dict(sorted(Counter(t["section"] for t in self.tasks).items(), key=lambda x: num(x[0]))),
             "по_формату": Counter(t["format"] for t in self.tasks), "по_статусу": Counter(t["status"] for t in self.tasks),
             "по_шагам": Counter(t["steps"] for t in self.tasks)}
        cov = Counter(c for t in self.tasks if t["track"] == "содержание" for c in t["concepts"])
        S["понятий_покрыто"] = sum(1 for c in self.C if cov[c] > 0)
        S["понятий_всего"] = len(self.C)
        S["понятия_без_заданий"] = [self.lab(c) for c in self.C if cov[c] == 0]
        S["понятия_мало_заданий"] = {self.lab(c): cov[c] for c in self.C if 0 < cov[c] < 3}
        S["умения_не_покрыты"] = {r: self.req[r]["text"][:90] for r in self.req if r not in S["по_умениям"]}
        if OUT_OF_SCOPE:
            out = {r: t for r, t in S["умения_не_покрыты"].items() if re.search(OUT_OF_SCOPE, self.req[r]["text"].strip().lower())}
            S["умения_не_покрыты"] = {r: t for r, t in S["умения_не_покрыты"].items() if r not in out}
            S["умения_вне_охвата"] = out      # карта, проекты, дискуссии, изображения — текстовый бенчмарк их не проверяет
        S["проблемы_сборки"] = self.problems
        S["пропущено_при_сборке"] = self.skipped
        return S


def write_md(tasks, S, path):
    title = f"Бенчмарк «{slice_title()}» — полный список заданий"
    L = [f"# {title}", "", f"Всего заданий: **{S['всего']}** (содержание — {S['по_дорожкам'].get('содержание', 0)}, структура программы — "
         f"{S['по_дорожкам'].get('структура', 0)}). Понятий покрыто: {S['понятий_покрыто']} из {S['понятий_всего']}.", ""]
    fam = None
    for t in sorted(tasks, key=lambda t: (list(FAMILY).index(t["family"]), t["id"])):
        if t["family"] != fam:
            fam = t["family"]
            L += ["", f"## {fam}. {FAMILY[fam][0]} — {sum(1 for x in tasks if x['family'] == fam)}", ""]
        L.append(f"**{t['id']}** · ФОП {t['requirement']} · эл. {t['element']} · {t['format']} · шагов {t['steps']} · {t['status']}  ")
        L.append(t["question"] + "  ")
        for o in t.get("options", []):
            L.append(f"{o['key']}) {o['text']}  ")
        unit = f" {t['check'].get('unit')}" if isinstance(t.get("check"), dict) and t["check"].get("unit") and t["format"] == "число" else ""
        L.append(f"*Ответ:* {t['answer']}{unit}  ")
        if t.get("explanation"):
            L.append(f"*Пояснение:* {t['explanation']}")
        L.append("")
    open(path, "w", encoding="utf-8").write("\n".join(L))


def main():
    final = "--итог" in sys.argv
    b = Builder()
    T.build(b)                                         # порядок семейств предмета: phys_tasks.build / hist_tasks.build
    dup = [i for i, n in Counter(t["id"] for t in b.tasks).items() if n > 1]
    if dup:
        b.problems.append(f"дубли id: {dup[:10]}")
    flagged, checked = b.apply_flags() if final else (0, False)
    b.weights()
    S = b.summary()
    if final:
        S["проверка_вариантов_моделью"] = {"проведена": checked, "помечено_заданий": flagged}
    write_jsonl(b.tasks, bn("задания.jsonl"))
    json.dump(S, open(bn("сводка.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_md(b.tasks, S, bn("задания.md"))
    tpl = open(os.path.join(HERE, "просмотр_шаблон.html"), encoding="utf-8").read()
    data = json.dumps({"title": slice_title(), "tasks": b.tasks, "summary": S, "families": {k: v[0] for k, v in FAMILY.items()},
                       "requirements": {r["code"]: r["text"] for r in b.srez["requirements"]},
                       "sections": {s["code"]: s["text"] for s in b.srez["sections"]}}, ensure_ascii=False)
    open(bn("просмотр.html"), "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/null", data.replace("</", "<\\/")))
    print(f"заданий {S['всего']}: {dict(S['по_дорожкам'])}; понятий покрыто {S['понятий_покрыто']} из {S['понятий_всего']}; "
          f"умений {len(S['по_умениям'])}, не покрыты {list(S['умения_не_покрыты'])}")
    if final:
        print(f"проверка вариантов моделью: {'помечено ' + str(flagged) if checked else 'не проводилась'}")
    if b.problems:
        print("ПРОБЛЕМЫ:", *b.problems[:30], sep="\n  ")
    if final:
        import bench_check
        sys.exit(bench_check.check(bn("задания.jsonl")))


if __name__ == "__main__":
    main()
