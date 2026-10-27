#!/usr/bin/env python3
"""Этап «цепочки»: кандидаты многошаговых расчётных задач из формул среза (семейство C2).

Глубина берётся из нормы ФОП: текст умения с ролью «решать» («в 1–2 действия», «2–3 действия», «из 2–3 уравнений»).
Перебираются цепочки ровно в пределах нормы своего класса: 7 кл. «1–2 действия» → только 2 (одно действие — это
обычные задания C), 8 кл. «2–3» → 2–3, 9 кл. «2–3 уравнения» → 2–3. Глубже нормы не строится.
Если норма числом не задана (10–11 классы: «с явно заданной физической моделью»), берётся 2–3.

Программа накопительная: в конфиге можно указать «формулы_прошлых_классов» — список формулы.json прошлых срезов.
Их формулы входят в цепочки, но в каждой цепочке должна быть хотя бы одна формула текущего класса
(в 8 классе: Q = c·m·Δt вместе с ρ = m/V из 7-го).

Вход: 3_знания/<срез>/формулы.json, срез.json; решения/цепочки_роли.json (этап цепочки_роли_llm: какая величина к какому
объекту относится, условия, правдоподобные значения); ручные_данные/<срез>/справочные_данные.json;
ручные_данные/<срез>/цепочки.json — необязательные правки поверх решений модели (для отладки).
Выход: 4_бенчмарк/<срез>/цепочки_кандидаты.json и .md. Решение «ок/нет» по наборам принимает этап цепочки_llm
(решения/цепочки.json); сборка заданий берёт только «ок».
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_common import C, SLICE, bn, dec, jdump, jload, kn, manual, resolve_role, slice_title  # noqa: E402
from chains_engine import Engine, group_id, norm_steps  # noqa: E402


def chain_manual():
    """Роли и решения модели (решения/цепочки_роли.json, решения/цепочки.json) + ручные правки поверх (если есть)."""
    man = {"роли": {}, "условия": {}, "значения": {}, "исключить_формулы": [], "решения": {}}
    if os.path.exists(dec("цепочки_роли.json")):
        for f in jload(dec("цепочки_роли.json")).get("формулы", []):
            man["роли"][f["id"]] = f.get("величины", {})
            if f.get("условие"):
                man["условия"][f["id"]] = f["условие"]
            if f.get("исключить"):
                man["исключить_формулы"].append(f["id"])
    if os.path.exists(dec("цепочки.json")):
        for g, x in jload(dec("цепочки.json")).items():
            man["решения"][g] = x.get("решение") if isinstance(x, dict) else x
    hand = jload(manual("цепочки.json")) if manual("цепочки.json") else {}
    for fid, sy in (hand.get("роли") or {}).items():
        for sym, ov in sy.items():
            man["роли"].setdefault(fid, {}).setdefault(sym, {}).update(ov)
    for k in ("условия", "значения", "решения"):
        man[k].update(hand.get(k) or {})
    man["исключить_формулы"] += hand.get("исключить_формулы") or []
    return man


def load_engine():
    if not os.path.exists(kn("формулы.json")):
        return None, None, "нет формулы.json"
    forms = jload(kn("формулы.json"))
    own = {f["id"] for f in forms.get("формулы", [])}
    for p in C.get("формулы_прошлых_классов") or []:
        if os.path.exists(p):
            old = jload(p)
            forms = {**forms, "формулы": forms["формулы"] + [f for f in old.get("формулы", []) if f["id"] not in {g["id"] for g in forms["формулы"]}],
                     "величины": {**old.get("величины", {}), **forms.get("величины", {})},
                     "обозначения": {**old.get("обозначения", {}), **forms.get("обозначения", {})},
                     "единицы": {**old.get("единицы", {}), **forms.get("единицы", {})}}
    s = jload(kn("срез.json"))
    lab = {c["id"]: c["label"] for c in s["concepts"]}
    sec = {c["label"]: c.get("section") for c in s["concepts"]}
    isa = defaultdict(set)
    for r in s["relations"]:
        if r["type"] == "is_a" and r["source"] in lab and r["target"] in lab:
            isa[lab[r["source"]]].add(lab[r["target"]])
    ref = jload(manual("справочные_данные.json")) if manual("справочные_данные.json") else {}
    man = chain_manual()
    code = resolve_role(s["requirements"], "решать")
    text = next((r["text"] for r in s["requirements"] if r["code"] == code), "")
    norm = norm_steps(text)
    eng = Engine(forms, isa, ref, man)
    eng.own = own
    eng.section = {fid: next((sec.get(c) for c in fo.concepts if sec.get(c)), None) for fid, fo in eng.F.items()}
    return eng, {"умение": code, "текст": text, "норма": norm}, None


def bounds(info):
    lo, hi = info["норма"] or (2, 3)
    return max(2, lo), max(2, min(hi, 4))


def current(eng, chains):
    """Только цепочки, где есть формула текущего класса."""
    return [c for c in chains if set(c.formulas()) & eng.own]


def main():
    eng, info, err = load_engine()
    if err:
        print(f"цепочки: {err}, пропуск"); return
    lo, hi = bounds(info)
    chains = current(eng, eng.enumerate(hi, lo))
    groups = defaultdict(list)
    for c in chains:
        groups[group_id(eng.F, c.formulas())].append(c)
    dec = (eng.manual.get("решения") or {})
    nmax = (info["норма"] or (2, 3))[1]
    out = []
    for gid, cs in groups.items():
        cs.sort(key=lambda c: c.cid(eng.F))
        ex = None
        for c in cs:
            ex = eng.instance(c, c.cid(eng.F))
            if ex:
                break
        forms = sorted(set(cs[0].formulas()))
        out.append({
            "набор": gid, "решение": dec.get(gid, ""), "шагов": cs[0].size,
            "выше_нормы": cs[0].size > nmax,
            "формулы": [f"{f}: {eng.F[f].text}" for f in forms],
            "разделы": sorted({str(eng.section.get(f)) for f in forms}),
            "цепочек": len(cs),
            "искомые": sorted({eng.F[c.root[0]].slots[c.root[1]].name for c in cs}),
            "пример": None if not ex else {"схема": eng.describe(c), "вопрос": ex["question"], "ответ": ex["answer"],
                                            "единица": ex["unit"], "решение": ex["explanation"]},
        })
    out.sort(key=lambda x: (x["шагов"], len(x["разделы"]), x["разделы"], x["формулы"]))
    S = {"срез": SLICE, "норма": info, "перебор_шагов": [lo, hi], "наборов": len(out), "цепочек": len(chains),
         "по_шагам": {str(k): sum(1 for x in out if x["шагов"] == k) for k in range(lo, hi + 1)},
         "без_чисел": sum(1 for x in out if not x["пример"]),
         "формулы_пропущены": eng.skipped, "наборы": out}
    jdump(S, bn("цепочки_кандидаты.json"))
    L = [f"# Цепочки формул — кандидаты · {slice_title()}", "",
         f"Норма ФОП (умение {info['умение']}): «{info['текст'][:110]}…» → "
         + (f"{info['норма'][0]}–{info['норма'][1]} действия." if info["норма"] else "числом не задана, взято 2–3."),
         f"Перебор: {lo}–{hi} действия. Наборов формул: **{len(out)}**, цепочек (разные искомые): {len(chains)}.", "",
         "Решение «ок/нет» по каждому набору принимает модель-проверяющий (этап `цепочки_llm`, `решения/цепочки.json`). "
         "«ок» открывает все цепочки набора (разные искомые величины). Ручная правка, если нужна, — `ручные_данные/" + SLICE + "/цепочки.json` → `решения`.", ""]
    if eng.skipped:
        L += ["Формулы, которые не вошли: " + "; ".join(f"{k} — {v}" for k, v in eng.skipped.items()), ""]
    k0 = None
    for x in out:
        if x["шагов"] != k0:
            k0 = x["шагов"]
            L += ["", f"## {k0} действия" + (" — выше нормы" if x["выше_нормы"] else ""), ""]
        mark = {"ок": "✅", "нет": "❌"}.get(x["решение"], "☐")
        L.append(f"### {mark} {x['набор']} · разделы {', '.join(x['разделы'])} · цепочек {x['цепочек']}")
        L.append("Формулы: " + " · ".join(f.split(': ', 1)[1] for f in x["формулы"]) + "  ")
        L.append("Искомые: " + ", ".join(x["искомые"]) + "  ")
        if x["пример"]:
            p = x["пример"]
            L.append(f"Пример: {p['вопрос']}  ")
            L.append(f"Ответ: {p['ответ']} {p['единица']} · {p['решение']}")
        else:
            L.append("Пример: числа не подобрались (проверьте наборы значений)")
        L.append("")
    open(bn("цепочки_кандидаты.md"), "w", encoding="utf-8").write("\n".join(L))
    print(f"цепочки: норма {info['норма']}, перебор {lo}–{hi}, наборов {len(out)}, цепочек {len(chains)}, без чисел {S['без_чисел']}")


if __name__ == "__main__":
    main()
