#!/usr/bin/env python3
"""Этап «знания»: решения/знания.json → 3_знания/<срез>/знания.jsonl.

Ещё раз проверяет каждую цитату (как этап «знания_llm») и раскрывает источники: урок ЦОК, ссылка, элемент ФОП, пункт ОГЭ.
Статус знания: подтверждено (покрытие полное), частично, без источника (нужна вычитка), ошибка источника.
id знания — K.<срез>.<понятие>.<номер>, не зависит от порядка понятий в решениях.
"""
import sys
from collections import Counter

from bench_common import C, Slice, dec, jload, kn, slug, write_jsonl
from llm_knowledge import check_entries, sources

STATUS = {"полное": "подтверждено", "частичное": "частично", "нет": "без источника"}


def main():
    sl = Slice()
    corpus, fop, exam_all = sources(sl)
    data = jload(dec("знания.json"))
    errs, out, missing = [], [], []
    for c in sl.s["concepts"]:
        items = data.get(c["id"])
        if not items:
            missing.append(c["label"]); continue
        errs += check_entries(c, items, corpus, fop, exam_all)
        for n, x in enumerate(items, 1):
            src = []
            for q in x.get("цитаты") or []:
                if q.get("uid") in corpus:
                    u = corpus[q["uid"]]
                    src.append({"type": "ЦОК", "uid": u["uid"], "quote": q["цитата"], "lesson": u["lesson"], "lesson_name": u["lesson_name"],
                                "task": u["task"], "url": u["lesson_url"]})
                elif q.get("источник") in ("ФОП", "ОГЭ", "ЕГЭ"):
                    src.append({"type": q["источник"], "code": q["код"], "quote": q["цитата"]})
            status = "ошибка источника" if x["вид"] == "ошибка_источника" else ("без источника" if not src else STATUS[x["покрытие"]])
            row = {"id": f"K.{C['срез']['имя']}.{slug(c['id'])}.{n:02d}", "concept_id": c["id"], "concept": c["label"], "ctype": c["ctype"],
                   "element": c["element"], "kind": x["вид"], "text": x["текст"], "status": status,
                   "note": x.get("примечание") or None, "sources": src}
            if x.get("даты"):          # история: дата в виде чисел — из неё строятся задания на хронологию
                row["dates"] = x["даты"]
            if x.get("суть"):          # история: то же без названия понятия — для заданий «каков итог / о ком речь»
                row["essence"] = x["суть"]
            out.append(row)
    write_jsonl(out, kn("знания.jsonl"))
    print(f"знаний {len(out)}; статусы {dict(Counter(o['status'] for o in out))}; понятий со знаниями {len(sl.s['concepts']) - len(missing)} из {len(sl.s['concepts'])}")
    if missing:
        print("без знаний:", "; ".join(missing))
    if errs:
        print(f"ОШИБКИ ({len(errs)}):", *errs[:40], sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
