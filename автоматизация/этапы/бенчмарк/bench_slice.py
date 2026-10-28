#!/usr/bin/env python3
"""Этап «срез»: из графа берутся понятия, элементы, разделы и умения одного класса предмета.

Конфиг: «граф» — папка с nodes.jsonl/edges.jsonl; «срез» — имя, предмет, уровень, классы, доп_понятия.
В срез входят понятия, впервые названные в элементах этих классов, и «доп_понятия» — понятия, которые в графе
есть только в пунктах ОГЭ, но по содержанию относятся к темам класса (список с основанием: ручные_данные/…/доп_понятия.json).
Вес понятия — доля учебного времени: доля элемента делится между его понятиями (main — 2, part — 1).

Необязательно (история):
  «курсы»   — {"всеобщая": ["1"], "россия": ["2", "3", …]}: к какому курсу относится раздел; у понятия и раздела появляется
              поле course (нужно для заданий на синхронность событий истории России и всеобщей истории);
  «разделы» — список кодов разделов: срез только по ним (пилот на части класса).
"""
import json
import os
from collections import defaultdict

from bench_common import C, GRAPH, jdump, jload, kn, manual

ROLE_W = {"main": 2.0, "part": 1.0}


def num(code):
    return [int(p) for p in code.split(".") if p.isdigit()]


def main():
    s = C["срез"]
    subj, level, grades = s["предмет"], s.get("уровень", "OOO"), set(s["классы"])
    N = {}
    for line in open(os.path.join(GRAPH, "nodes.jsonl"), encoding="utf-8"):
        n = json.loads(line)
        N[n["id"]] = n
    E = [json.loads(l) for l in open(os.path.join(GRAPH, "edges.jsonl"), encoding="utf-8")]

    def of(t):
        return {i: n for i, n in N.items() if n.get("subject") == subj and n.get("level", level) == level and n.get("grade") in grades and n["type"] == t}
    el, sec, req = of("element"), of("section"), of("requirement")
    if not el:
        raise SystemExit(f"в графе нет элементов: {subj} {level} {sorted(grades)}")
    multi = len(grades) > 1
    code_of = {i: (f"{n['grade']}:{n['code']}" if multi else n["code"]) for i, n in {**el, **sec}.items()}
    sec_of = {e["source"]: e["target"] for e in E if e["type"] == "is_part_of" and e["source"] in el}
    for i, n in el.items():
        if sec_of.get(i) not in sec:
            # элемент вне разделов (история 6–9 кл.: «Наш край…» прямо в классе) — сам себе раздел с тем же кодом
            sec[i] = {**n, "type": "section"}
            sec_of[i] = i
    only = s.get("разделы")
    if only:
        only = {str(x) for x in only}
        sec = {i: n for i, n in sec.items() if code_of[i] in only}
        el = {i: n for i, n in el.items() if sec_of.get(i) in sec}
        if not el:
            raise SystemExit(f"разделы {sorted(only)}: в срезе нет элементов")
    el_by_code = {code_of[i]: i for i in el}
    course_of = {}
    for name, codes in (s.get("курсы") or {}).items():
        for cd in codes:
            course_of[str(cd)] = name

    core = {i for i, n in N.items() if n["type"] == "concept" and n.get("subject") == subj and n.get("first_grade") in grades
            and (not only or n.get("first_element") in el)}
    by_label = {n["label"]: i for i, n in N.items() if n["type"] == "concept" and n.get("subject") == subj}
    extra = {}
    dp = s.get("доп_понятия")
    if isinstance(dp, str):
        lst = jload(manual(dp)) if manual(dp) else []
        if not lst:
            print(f"доп_понятия: файла {dp} в ручных данных нет — в срез входят только понятия, впервые названные в классе")
    else:
        lst = dp or []
    for x in lst:
        if x["метка"] not in by_label:
            raise SystemExit(f"доп_понятия: в графе нет понятия «{x['метка']}»")
        if x["элемент"] not in el_by_code:
            raise SystemExit(f"доп_понятия: нет элемента {x['элемент']} для «{x['метка']}»")
        extra[by_label[x["метка"]]] = (el_by_code[x["элемент"]], x.get("основание", ""))
    ids = core | set(extra)

    ment, per_el = defaultdict(list), defaultdict(list)
    for e in E:
        if e["type"] == "mentions" and e["source"] in el and e["target"] in ids:
            ment[e["target"]].append((e["source"], e.get("role", "part"), e.get("evidence", "")))
            per_el[e["source"]].append((e["target"], e.get("role", "part")))
    for c, (e_, _) in extra.items():
        per_el[e_].append((c, "part"))
    weight = defaultdict(float)
    for e_, lst2 in per_el.items():
        tot = sum(ROLE_W[r] for _, r in lst2)
        for c, r in lst2:
            weight[c] += (el[e_].get("share_pct") or 0) * ROLE_W[r] / tot
    lessons_of = defaultdict(list)
    for e in E:
        if e["type"] == "appears_in" and e["source"] in el:
            lessons_of[e["source"]].append(e["target"])
    exam_text = {i: n["text"] for i, n in N.items() if n["type"] == "exam_element"}

    concepts = []
    for c in sorted(ids, key=lambda i: N[i]["label"]):
        n = N[c]
        els = [m[0] for m in ment[c]] or [extra[c][0]]
        first = n.get("first_element") if c in core else extra[c][0]
        if first not in el:
            first = els[0]
        concepts.append({
            "id": c, "label": n["label"], "ctype": n["ctype"], "aliases": n.get("aliases", []),
            "origin": "класс" if c in core else "ОГЭ, тема класса", "origin_note": None if c in core else extra[c][1],
            "element": code_of[first], "elements": sorted({code_of[x] for x in els}, key=num),
            "section": code_of[sec_of[first]], "evidence": [m[2] for m in ment[c]],
            "formulas_kodifikator": n.get("formulas", []), "exam_items": n.get("exam_items", []),
            "exam_texts": {x: exam_text.get(x, "") for x in n.get("exam_items", [])},
            "grades": n.get("grades", []), "weight_pct": round(weight[c], 4),
        })
        if course_of:
            concepts[-1]["course"] = course_of.get(concepts[-1]["section"])
    rel = [e for e in E if e["type"] in ("prerequisites_for", "is_a", "part_of", "verifies", "participant_of")
           and e["source"] in ids and e["target"] in ids]
    out = {
        "описание": f"Срез графа: {subj}, классы {sorted(grades)}. Этап «срез».",
        "срез": s["имя"], "предмет": subj, "классы": sorted(grades),
        "sections": [{"id": i, "code": code_of[i], "text": n["text"], "hours": n.get("hours"), "share_pct": n.get("share_pct"),
                      **({"course": course_of.get(code_of[i])} if course_of else {})}
                     for i, n in sorted(sec.items(), key=lambda x: num(code_of[x[0]]))],
        "elements": [{"id": i, "code": code_of[i], "text": n["text"], "hours": n.get("hours"), "share_pct": n.get("share_pct"),
                      "category": n.get("category"), "section": code_of[sec_of[i]], "lessons": lessons_of[i]}
                     for i, n in sorted(el.items(), key=lambda x: num(code_of[x[0]]))],
        "requirements": [{"id": i, "code": n["code"], "text": n["text"], "verb": n.get("verb")}
                         for i, n in sorted(req.items(), key=lambda x: num(x[1]["code"]))],
        "concepts": concepts, "relations": rel,
        # все пункты экзамена этого уровня по предмету: цитату из ОГЭ можно брать и из пункта, не привязанного к понятию в графе
        "exam_texts": {i: t for i, t in sorted(exam_text.items()) if i.startswith(f"{level}.{subj}.")},
    }
    jdump(out, kn("срез.json"))
    tw = sum(c["weight_pct"] for c in concepts)
    print(f"понятий: {len(concepts)} (класс: {len(core)}, из ОГЭ: {len(extra)}), элементов {len(el)}, связей {len(rel)}, сумма весов {tw:.2f}%")


if __name__ == "__main__":
    main()
