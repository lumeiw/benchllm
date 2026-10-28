#!/usr/bin/env python3
"""Этап «знания_llm»: модель наполняет понятия среза знаниями с цитатами (в пилоте это делал Claude в чате).

Модель получает по N понятий (llm.понятий_за_запрос_знания) с кандидатами-цитатами из корпуса ЦОК, текстом элемента
программы и пунктами ОГЭ. Каждый ответ проверяется: цитата — точная подстрока источника, виды знаний — из списка,
покрытие согласовано с цитатами. Ответ с ошибками возвращается модели на исправление.
Выход: решения/знания.json — {id понятия: [знания]}; собирает их этап «знания».
replay: берутся решения пилота (конфиг replay.знания).
"""
from bench_common import EXAM, PROFILE, C, Slice, dec, jdump, jl, jload, kn, mode, norm, replay_path, setting
from llm import LLM, chunks, prompt, run_stage

KINDS = PROFILE.KNOWLEDGE_KINDS          # виды знаний предмета (физика/phys_profile.py, история/hist_profile.py)
COVER = {"полное", "частичное", "нет"}
EXAM_SRC = ("ОГЭ", "ЕГЭ")


def sources(sl):
    corpus = {u["uid"]: u for u in jl(kn("источники/цок_корпус.jsonl"))}
    fop = {e["code"]: e["text"] for e in sl.s["elements"]}
    return corpus, fop, sl.s.get("exam_texts", {})


def check_entries(c, items, corpus, fop, exam_all=None):
    """Ошибки в знаниях одного понятия (общая проверка для этапов «знания_llm» и «знания»)."""
    errs = []
    if not isinstance(items, list) or not items:
        return [f"{c['label']}: нет знаний"]
    if len(items) > 8:
        errs.append(f"{c['label']}: знаний больше 8")
    exam = {**(exam_all or {}), **c.get("exam_texts", {})}
    for k, x in enumerate(items, 1):
        where = f"{c['label']} #{k}"
        if not isinstance(x, dict):
            errs.append(f"{where}: не объект"); continue
        if x.get("вид") not in KINDS:
            errs.append(f"{where}: вид {x.get('вид')!r} не из списка")
        if not (x.get("текст") or "").strip():
            errs.append(f"{where}: пустой текст")
        if x.get("покрытие") not in COVER:
            errs.append(f"{where}: покрытие должно быть полное / частичное / нет")
        qs = x.get("цитаты") or []
        if not isinstance(qs, list):
            errs.append(f"{where}: цитаты — список"); continue
        if not qs and x.get("покрытие") in ("полное", "частичное") and x.get("вид") != "ошибка_источника":
            errs.append(f"{where}: покрытие «{x.get('покрытие')}», но цитат нет")
        if qs and x.get("покрытие") == "нет":
            errs.append(f"{where}: есть цитаты, а покрытие «нет»")
        for q in qs:
            t = norm(q.get("цитата"))
            if len(t) < 6:
                errs.append(f"{where}: слишком короткая цитата {t!r}"); continue
            if q.get("uid"):
                u = corpus.get(q["uid"])
                if not u:
                    errs.append(f"{where}: нет единицы {q['uid']}")
                elif t not in norm(u["text"]):
                    errs.append(f"{where}: цитата не найдена дословно в {q['uid']}: {t[:80]!r}")
            elif q.get("источник") == "ФОП":
                if q.get("код") not in c["elements"] or t not in norm(fop.get(q.get("код"), "")):
                    errs.append(f"{where}: цитата не найдена в элементе ФОП {q.get('код')} (допустимы {c['elements']})")
            elif q.get("источник") in EXAM_SRC:
                if q.get("код") not in exam or t not in norm(exam[q["код"]]):
                    errs.append(f"{where}: цитата не найдена в пункте {q.get('источник')} {q.get('код')}" + (f" (пункты понятия: {list(c['exam_texts'])})" if c.get("exam_texts") else ""))
            else:
                errs.append(f"{where}: у цитаты нужен uid или источник ФОП/{EXAM}")
        errs += PROFILE.knowledge_checks(c, x, qs, where)      # проверки предмета: у истории — даты по цитатам и «суть»
    return errs



def main():
    sl = Slice()
    corpus, fop, exam_all = sources(sl)
    cand = jload(kn("кандидаты.json"))
    el_text = {e["code"]: e["text"] for e in sl.s["elements"]}
    old = jload(replay_path("знания")) if mode() == "replay" and replay_path("знания") else {}
    ctx = {}
    llm = LLM("знания", replay=lambda tid: {"результат": [{"id": cid, "знания": old.get(cid, [])} for cid in ctx[tid]]})
    tasks, vals = [], {}
    for i, batch in enumerate(chunks(sl.s["concepts"], int(setting("понятий_за_запрос_знания", 4)))):
        tid = f"{C['срез']['имя']}.{i + 1:03d}"
        ctx[tid] = [c["id"] for c in batch]
        user = {"предмет": sl.s["предмет"], "классы": sl.s["классы"], "понятия": [{
            "id": c["id"], "название": c["label"], "тип": c["ctype"], "синонимы": c.get("aliases", []),
            "элемент": f"{c['element']} {el_text.get(c['element'], '')}", "основания": c.get("evidence", []),
            "пункты_ОГЭ": c.get("exam_texts", {}), "формулы_кодификатора": c.get("formulas_kodifikator", []),
            "кандидаты": cand.get(c["id"], [])} for c in batch]}
        user["понятия"] = [PROFILE.knowledge_request(u, c, EXAM) for u, c in zip(user["понятия"], batch)]
        tasks.append((tid, prompt("знания"), user))

        def v(obj, batch=batch):
            res = obj.get("результат") if isinstance(obj, dict) else None
            if not isinstance(res, list):
                return ['нужен объект {"результат": [...]}']
            want, got = [c["id"] for c in batch], [x.get("id") for x in res if isinstance(x, dict)]
            errs = [] if got == want else [f"нужно по одному объекту на понятие в порядке {want}; получено {got}"]
            by = {x.get("id"): x for x in res if isinstance(x, dict)}
            for c in batch:
                if c["id"] in by:
                    errs += check_entries(c, by[c["id"]].get("знания"), corpus, fop, exam_all)
            return errs
        vals[tid] = v
    answers = llm.ask_many(tasks, lambda tid: vals[tid])
    llm.report()
    out = {}
    for tid, _, _ in tasks:
        for x in answers[tid]["результат"]:
            out[x["id"]] = x["знания"]
    jdump(out, dec("знания.json"))
    print(f"понятий {len(out)}, знаний {sum(len(v) for v in out.values())}")


if __name__ == "__main__":
    run_stage(main)
