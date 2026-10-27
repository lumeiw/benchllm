#!/usr/bin/env python3
"""Сводная таблица по всем прогонам: одна строка — одна модель (прогоны/*.jsonl, архивы *.old.jsonl не берутся).

  python compare_runs.py           # таблица в консоль + прогоны/сводка_моделей.md и прогоны/сводка_моделей.csv

Столбцы:
  решено      — сколько заданий из задания.jsonl есть в файле прогона;
  авто        — точность на заданиях с автоматической проверкой (выбор, порядок, число, текст);
  развёрнутые — точность на развёрнутых ответах, оценённых судьёй, и кто судил;
  всего, взвеш. — по всем оценённым заданиям; взвешенная — с весами заданий (как строка all в gigachat_bench.py).
Сравнивать модели честнее всего по столбцу «авто»: там нет судьи.
"""
import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "прогоны"
AUTO = {"choice", "sequence", "number", "value_error", "text"}
FORMATS = [("один_ответ", "один ответ"), ("несколько_ответов", "несколько"), ("число", "число"),
           ("порядок", "порядок"), ("развёрнутый", "развёрнутый")]


def pct(xs):
    return f"{100 * sum(xs) / len(xs):.1f}" if xs else "—"


def main():
    tasks = {}
    for line in open(HERE / "задания.jsonl", encoding="utf-8"):
        if line.strip():
            t = json.loads(line)
            tasks[t["id"]] = t
    n_auto = sum(1 for t in tasks.values() if t["check"]["type"] in AUTO)
    n_rub = sum(1 for t in tasks.values() if t["check"]["type"] == "rubric")

    rows = []
    for f in sorted(RUNS.glob("*.jsonl")):
        if f.name.endswith(".old.jsonl"):
            continue
        recs = {}
        for line in open(f, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("id") in tasks:
                    recs[r["id"]] = r                      # последняя запись по заданию, как в gigachat_bench.py
        if not recs:
            continue
        auto, rub, allw, by_fmt, judges = [], [], [], {k: [] for k, _ in FORMATS}, Counter()
        for tid, r in recs.items():
            t, s = tasks[tid], r.get("score")
            if s is None:
                continue
            s = float(s)
            (auto if t["check"]["type"] in AUTO else rub).append(s)
            if t["check"]["type"] == "rubric":
                judges[r.get("judge_model") or "не записан"] += 1
            allw.append((s, t.get("weight", 1.0)))
            if t["format"] in by_fmt:
                by_fmt[t["format"]].append(s)
        w = sum(x for _, x in allw)
        rows.append({
            "модель": f.stem,
            "решено": f"{len(recs)}/{len(tasks)}",
            "авто, %": pct(auto),
            "авто, n": len(auto),
            "развёрнутые, %": pct(rub),
            "развёрнутые, n": len(rub),
            "судья": ", ".join(k for k, _ in judges.most_common()) or "—",
            "всего, %": pct([s for s, _ in allw]),
            "взвеш., %": f"{100 * sum(s * x for s, x in allw) / w:.1f}" if w else "—",
            **{f"{title}, %": pct(by_fmt[k]) for k, title in FORMATS},
        })
    if not rows:
        print("В прогоны/ нет файлов с ответами.")
        return
    rows.sort(key=lambda r: -float(r["авто, %"]) if r["авто, %"] != "—" else 0)

    cols = list(rows[0])
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    note = (f"\nЗаданий: {len(tasks)}; с автоматической проверкой — {n_auto}, развёрнутых — {n_rub}. "
            "«авто» — главный столбец для сравнения: там нет судьи. Развёрнутые сравнимы, только если судья один и тот же.")
    print("\n".join(md) + "\n" + note)
    (RUNS / "сводка_моделей.md").write_text("# Сводка по моделям\n\n" + "\n".join(md) + "\n" + note + "\n", encoding="utf-8")
    with open(RUNS / "сводка_моделей.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, delimiter=";")
        wr.writeheader()
        wr.writerows(rows)
    print(f"\nСохранено: прогоны/сводка_моделей.md, прогоны/сводка_моделей.csv")


if __name__ == "__main__":
    main()
