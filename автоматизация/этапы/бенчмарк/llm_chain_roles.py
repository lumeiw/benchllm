#!/usr/bin/env python3
"""Этап «цепочки_роли_llm»: модель размечает величины в формулах среза для цепочек (семейство C2).

Для каждой формулы и каждой её буквы: к какому объекту относится величина (одно слово, одинаковое во всех формулах для
одного и того же объекта: «тело», «жидкость», «пружина», «вода», «нагреватель»…), как назвать её в условии задачи и
5–8 правдоподобных школьных значений в СИ. Плюс условие применимости формулы одной фразой («Тело полностью погружено
в жидкость.») и пометка «исключить», если формула не годится для сцепления (например, это определение без расчёта).
Зачем: буква одна, а величины разные (ρ тела и ρ жидкости) — без ролей программа сцепит их неверно.

Формулы прошлых классов (конфиг «формулы_прошлых_классов») размечаются вместе с текущими.
Выход: решения/цепочки_роли.json. replay: конфиг replay.цепочки_роли.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_common import C, dec, jdump, jload, kn, mode, replay_path  # noqa: E402
from chains_engine import Formula  # noqa: E402
from llm import LLM, prompt, run_stage  # noqa: E402


def all_formulas():
    forms = jload(kn("формулы.json"))
    own = {f["id"] for f in forms.get("формулы", [])}
    for p in C.get("формулы_прошлых_классов") or []:
        if os.path.exists(p):
            old = jload(p)
            forms = {**forms, "формулы": forms["формулы"] + [f for f in old.get("формулы", []) if f["id"] not in {g["id"] for g in forms["формулы"]}],
                     "величины": {**old.get("величины", {}), **forms.get("величины", {})},
                     "обозначения": {**old.get("обозначения", {}), **forms.get("обозначения", {})},
                     "единицы": {**old.get("единицы", {}), **forms.get("единицы", {})}}
    return forms


def main():
    if not os.path.exists(kn("формулы.json")):
        jdump({"формулы": []}, dec("цепочки_роли.json")); print("нет формулы.json — пропуск"); return
    forms = all_formulas()
    items = []
    for f in forms["формулы"]:
        fo = Formula(f, forms, {}, {"Ускорение свободного падения"})
        if not fo.ok:
            continue
        items.append({"id": f["id"], "формула": f["text"], "понятия": f.get("concepts", []), "примечание": f.get("note", ""),
                      "величины": {t: {"понятие": s.concept, "единица": s.unit} for t, s in fo.slots.items()}})
    if not items:
        jdump({"формулы": []}, dec("цепочки_роли.json")); print("формул для цепочек нет"); return
    want = {x["id"]: set(x["величины"]) for x in items}

    def validate(obj):
        res = obj.get("формулы") if isinstance(obj, dict) else None
        if not isinstance(res, list):
            return ['нужен объект {"формулы": [...]}']
        errs, got = [], {}
        for f in res:
            fid = f.get("id") if isinstance(f, dict) else None
            if fid not in want:
                errs.append(f"лишняя или безымянная формула: {fid}"); continue
            got[fid] = f
            v = f.get("величины")
            if not isinstance(v, dict) or set(v) != want[fid]:
                errs.append(f"{fid}: «величины» — ровно буквы {sorted(want[fid])}"); continue
            for sym, d in v.items():
                if not isinstance(d, dict) or not str(d.get("объект", "")).strip() or not str(d.get("назв", "")).strip():
                    errs.append(f"{fid}.{sym}: нужны «объект» и «назв»")
                    continue
                vals = d.get("значения")
                const = items[[x["id"] for x in items].index(fid)]["величины"][sym]["понятие"] == "Ускорение свободного падения"
                if not const and (not isinstance(vals, list) or not 4 <= len(vals) <= 10
                                  or any(not isinstance(x, (int, float)) or x <= 0 for x in vals)):
                    errs.append(f"{fid}.{sym}: «значения» — 4–10 положительных чисел в СИ")
            if not isinstance(f.get("исключить", False), bool):
                errs.append(f"{fid}: «исключить» — true или false")
        miss = set(want) - set(got)
        if miss:
            errs.append(f"нет формул: {sorted(miss)}")
        return errs

    old = jload(replay_path("цепочки_роли")) if mode() == "replay" and replay_path("цепочки_роли") else None
    llm = LLM("цепочки_роли", replay=lambda tid: old)
    tid = f"{C['срез']['имя']}.цепочки_роли"
    obj = llm.ask_many([(tid, prompt("цепочки_роли"), {"классы": C["срез"]["классы"], "формулы": items})], lambda _: validate)[tid]
    llm.report()
    if obj is None:
        raise SystemExit("нет разметки величин")
    errs = validate(obj)
    if errs:
        print("ОШИБКИ:", *errs[:30], sep="\n  "); raise SystemExit(1)
    jdump(obj, dec("цепочки_роли.json"))
    print(f"размечено формул {len(obj['формулы'])}, исключено {sum(1 for f in obj['формулы'] if f.get('исключить'))}")


if __name__ == "__main__":
    run_stage(main)
