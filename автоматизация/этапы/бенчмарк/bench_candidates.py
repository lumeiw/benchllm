#!/usr/bin/env python3
"""Этап «знания_кандидаты»: для каждого понятия среза — предложения корпуса ЦОК, где оно, вероятно, объясняется.

Поиск по основам слов названия и синонимов (первые буквы слова без окончания) + необязательный ручной словарь
поисковых синонимов (ручные_данные/…/поиск_синонимы.json: {"Механическая работа": [["работ"]], …}).
Ранжирование: доля найденных основ, вид единицы (решения тестов и страницы выше субтитров), признаки определения
(«называют», «—», «единица», «формула», «закон», «прибор для» …), короткие предложения выше длинных.
Выход: 3_знания/<срез>/кандидаты.json — {id понятия: [{uid, урок, вид, текст}]}; текст — предложение, дословная подстрока единицы.
"""
import re

from bench_common import SUBJECT, Slice, jdump, jl, jload, kn, manual, setting

STOP = {"тела", "тело", "тел", "вещества", "помощью", "при", "для", "его", "ее", "её", "от", "по", "и", "в", "на", "с", "их", "или",
        "других", "различными", "природе", "технике", "живой", "неживой", "одной", "прямой", "единице", "объёма", "частиц"}
DEF = re.compile(r"называ|—|–|это |единиц|измеря|формул|закон|равн|зависит|обознача|прибор для|служит|применя|использу|основан")
if SUBJECT == "history":
    # история: признаки определения, даты, участники, причины и итоги
    DEF = re.compile(r"называ|—|–|это |(?<!\d)\d{3,4}(?!\d)|\bв(ек|\.)|правил|правлен|родил|возглав|причин|итог|результат|привел|привёл|"
                     r"значени|в ходе|участ|основал|принят|заключ|подписа|произош|состоял|началась|началось|завершил")
KIND_W = {"тест_решение": 3, "страница": 3, "интерактив": 2, "тест_ответ": 2, "субтитры": 1, "цели_урока": 0}
SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"(0-9])")


def word_stems(label):
    out = []
    for w in re.findall(r"[А-Яа-яЁёA-Za-z]+", label.lower()):
        if w in STOP or len(w) < 3:
            continue
        out.append(w[:max(4, len(w) - 2)] if len(w) > 5 else w[:-1] if len(w) > 3 else w)
    return [x.replace("ё", "е") for x in out]


def main():
    sl = Slice()
    K = int(setting("кандидатов_на_понятие_знания", 25))
    syn = jload(manual("поиск_синонимы.json")) if manual("поиск_синонимы.json") else {}
    units = jl(kn("источники/цок_корпус.jsonl"))
    sents = []
    for u in units:
        for s in SPLIT.split(u["text"]):
            if 12 <= len(s) <= 450 and "[___]" not in s and not s.lstrip().startswith("{") and "grid-area" not in s:
                sents.append((u, s, s.lower().replace("ё", "е")))
    out, empty = {}, []
    for c in sl.s["concepts"]:
        groups = [[x.replace("ё", "е") for x in g] for g in syn.get(c["label"], [])] or [word_stems(c["label"])]
        groups += [word_stems(a) for a in c.get("aliases", [])]
        groups = [g for g in groups if g]
        res = []
        for u, s, low in sents:
            best = max((sum(1 for x in g if x in low) / len(g) for g in groups), default=0)
            if best >= 0.99 or (best >= 0.66 and len(groups[0]) >= 3):
                sc = best * 3 + KIND_W.get(u["kind"], 1) + (2 if DEF.search(low) else 0) - len(s) / 400
                res.append((-sc, u["uid"], s, u))
        res.sort(key=lambda x: (x[0], x[1], x[2]))
        seen, lst = set(), []
        for _, uid, s, u in res:
            key = re.sub(r"\W", "", s.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            lst.append({"uid": uid, "урок": f"{u['lesson']}. {u['lesson_name']}", "вид": u["kind"], "текст": s})
            if len(lst) >= K:
                break
        out[c["id"]] = lst
        if not lst:
            empty.append(c["label"])
    jdump(out, kn("кандидаты.json"))
    print(f"понятий {len(out)}, кандидатов {sum(len(v) for v in out.values())}; без кандидатов {len(empty)}" + (f": {', '.join(empty[:15])}" if empty else ""))


if __name__ == "__main__":
    main()
