#!/usr/bin/env python3
"""Этап «цепочки_llm»: модель-проверяющий решает, какие наборы формул дают осмысленную многошаговую задачу.

Вход: 4_бенчмарк/<срез>/цепочки_кандидаты.json (этап «цепочки»): набор формул, искомые величины, пример задачи с числами
и решением. Модель отвечает «ок» или «нет» с причиной. Пачки по llm.наборов_за_запрос_цепочки (по умолчанию 10).
Выход: решения/цепочки.json — {набор: {решение, причина}}; после записи пересобирается цепочки_кандидаты.md с отметками.
replay: конфиг replay.цепочки.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # общие модули: этапы/бенчмарк/
from bench_common import C, bn, dec, jdump, jload, mode, replay_path, setting  # noqa: E402
from llm import LLM, chunks, prompt, run_stage  # noqa: E402


def main():
    if not os.path.exists(bn("цепочки_кандидаты.json")):
        jdump({}, dec("цепочки.json")); print("нет кандидатов — пропуск"); return
    groups = [g for g in jload(bn("цепочки_кандидаты.json"))["наборы"] if g.get("пример")]
    old = jload(replay_path("цепочки")) if mode() == "replay" and replay_path("цепочки") else {}
    ctx = {}
    llm = LLM("цепочки", replay=lambda tid: {"результат": [
        {"набор": g, **(old.get(g) or {"решение": "нет", "причина": "нет решения в replay"})} for g in ctx[tid]]})
    reqs, vals = [], {}
    for n, batch in enumerate(chunks(groups, int(setting("наборов_за_запрос_цепочки", 10)))):
        tid = f"{C['срез']['имя']}.цепочки.{n + 1:03d}"
        ctx[tid] = [g["набор"] for g in batch]
        reqs.append((tid, prompt("цепочки"), {"классы": C["срез"]["классы"], "наборы": [
            {"набор": g["набор"], "шагов": g["шагов"], "формулы": g["формулы"], "искомые": g["искомые"],
             "пример": {k: g["пример"][k] for k in ("вопрос", "решение", "ответ", "единица")}} for g in batch]}))

        def v(obj, want=ctx[tid]):
            res = obj.get("результат") if isinstance(obj, dict) else None
            if not isinstance(res, list):
                return ['нужен объект {"результат": [...]}']
            got = [x.get("набор") for x in res if isinstance(x, dict)]
            errs = [] if got == want else [f"нужно по объекту на набор в порядке {want}; получено {got}"]
            for x in res:
                if isinstance(x, dict) and x.get("решение") not in ("ок", "нет"):
                    errs.append(f"{x.get('набор')}: «решение» — «ок» или «нет»")
                if isinstance(x, dict) and not str(x.get("причина", "")).strip():
                    errs.append(f"{x.get('набор')}: нужна «причина»")
            return errs
        vals[tid] = v
    ans = llm.ask_many(reqs, lambda tid: vals[tid])
    llm.report()
    out = {}
    for tid, _, _ in reqs:
        for x in ans[tid]["результат"]:
            out[x["набор"]] = {"решение": x["решение"], "причина": x.get("причина", "")}
    jdump(out, dec("цепочки.json"))
    import bench_chains
    bench_chains.main()   # отметки ✅/❌ в цепочки_кандидаты.md
    print(f"наборов {len(out)}: ок {sum(1 for x in out.values() if x['решение'] == 'ок')}, нет {sum(1 for x in out.values() if x['решение'] == 'нет')}")


if __name__ == "__main__":
    run_stage(main)
