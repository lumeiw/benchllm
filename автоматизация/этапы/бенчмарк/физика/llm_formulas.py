#!/usr/bin/env python3
"""Этап «формулы_llm»: справочник формул, обозначений и единиц класса (для семейств заданий F).

Один запрос на срез. Проверка: понятия — из среза, запись формулы согласована с видом (div: a = b / c, mul: a = b·c[·d]),
у формул вида other есть три неверные записи, все символы описаны в «величинах».
Если в срезе нет величин (история) — этап пишет пустой справочник.
Выход: решения/формулы.json и копия в 3_знания/<срез>/формулы.json. replay: конфиг replay.формулы.
"""
import re
import shutil

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # общие модули: этапы/бенчмарк/
from bench_common import C, Slice, dec, jdump, jl, jload, kn, mode, replay_path
from llm import LLM, prompt, run_stage

EMPTY = {"величины": {}, "формулы": [], "единицы_через_основные": [], "обозначения": {}, "единицы": {}}


def compact(s):
    return re.sub(r"\s+", "", s or "")


def validate_formulas(obj, labels):
    errs = []
    if not isinstance(obj, dict):
        return ["нужен объект"]
    for k in EMPTY:
        if k not in obj:
            errs.append(f"нет раздела «{k}»")
    if errs:
        return errs
    V = obj["величины"]
    ids = set()
    for f in obj["формулы"]:
        fid = f.get("id", "?")
        if not str(fid).startswith("F.") or fid in ids:
            errs.append(f"{fid}: id должен начинаться с F. и не повторяться")
        ids.add(fid)
        cs = f.get("concepts") or []
        bad = [c for c in cs if c not in labels]
        if not cs or bad:
            errs.append(f"{fid}: понятия не из среза: {bad or 'пусто'}")
        kind, parts, text = f.get("kind"), f.get("parts") or [], f.get("text", "")
        rhs = text.split("=", 1)[1] if "=" in text else ""
        if kind == "div":
            if len(parts) != 3 or compact(rhs) != compact(f"{parts[1]} / {parts[2]}"):
                errs.append(f"{fid}: для div нужно text = «a = b / c» и parts [a, b, c]")
        elif kind == "mul":
            if len(parts) not in (3, 4) or compact(rhs) != compact("·".join(parts[1:])):
                errs.append(f"{fid}: для mul нужно text = «a = b·c[·d]» и parts по порядку")
        elif kind == "other":
            w = f.get("неверные") or []
            if len(w) != 3 or len(set(w)) != 3 or text in w:
                errs.append(f"{fid}: для other нужны три разные неверные записи")
        else:
            errs.append(f"{fid}: kind — div, mul или other")
        for p in parts:
            if p not in V:
                errs.append(f"{fid}: символ {p!r} не описан в «величинах»")
    for k in ("обозначения", "единицы"):
        bad = [x for x in obj[k] if x not in labels]
        if bad:
            errs.append(f"{k}: названия не из среза: {bad[:8]}")
    for e in obj["единицы_через_основные"]:
        if e.get("понятие") not in labels or not e.get("unit") or not e.get("is"):
            errs.append(f"единицы_через_основные: {e} — нужны unit, is и понятие из среза")
    return errs


def main():
    sl = Slice()
    labels = set(sl.L)
    qty = [c for c in sl.s["concepts"] if c["ctype"] in ("величина", "закон")]
    if not qty:
        jdump(EMPTY, dec("формулы.json"))
        shutil.copy(dec("формулы.json"), kn("формулы.json"))
        print("в срезе нет величин и законов — справочник пустой")
        return
    know = {}
    for k in jl(kn("знания.jsonl")):
        if k["kind"] in ("определение", "формула", "единица", "обозначение", "формулировка") and k["status"] != "ошибка источника":
            know.setdefault(k["concept_id"], []).append(k["text"])
    old = jload(replay_path("формулы")) if mode() == "replay" and replay_path("формулы") else None
    llm = LLM("формулы", replay=lambda tid: old)
    user = {"классы": sl.s["классы"], "все_названия": sorted(labels), "понятия": [
        {"название": c["label"], "тип": c["ctype"], "знания": know.get(c["id"], []), "формулы_кодификатора": c.get("formulas_kodifikator", [])}
        for c in qty]}
    tid = f"{C['срез']['имя']}.формулы"
    obj = llm.ask_many([(tid, prompt("формулы"), user)], lambda _: (lambda o: validate_formulas(o, labels)))[tid]
    llm.report()
    if obj is None:
        raise SystemExit("нет справочника формул")
    errs = validate_formulas(obj, labels)
    if errs:
        print("ОШИБКИ:", *errs[:30], sep="\n  ")
        raise SystemExit(1)
    jdump(obj, dec("формулы.json"))
    shutil.copy(dec("формулы.json"), kn("формулы.json"))
    print(f"формул {len(obj['формулы'])}, единиц {len(obj['единицы'])}, обозначений {len(obj['обозначения'])}")


if __name__ == "__main__":
    run_stage(main)
