#!/usr/bin/env python3
"""ИС ОЗЯМ-ФОП · автоматизация: разметка документа ФОП и построение графа одной командой.

  python3 run.py конфиги/проект_2024.json                 весь пайплайн
  python3 run.py конфиги/проект_2024.json --list          список этапов
  python3 run.py конфиги/проект_2024.json --from понятия_llm --to граф
  python3 run.py конфиги/проект_2024.json --only связи_llm
  python3 run.py конфиги/проект_2024.json --llm replay    вместо модели — прошлые решения (проверка воспроизводимости)
  python3 run.py конфиги/проект_2024.json --llm manual    запросы к модели файлами (ответить можно любой моделью)

Результат — папка прогона (по умолчанию прогоны/<имя конфига>/) с manifest.json: из какого документа,
какой моделью и когда собрано. Существующие папки проекта не меняются.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

AUTO = os.path.dirname(os.path.abspath(__file__))
ST = os.path.join(AUTO, "этапы")


def A(*p):
    return os.path.join(*p)


def stages_bench(cfg):
    """Части 3–4: знания и задания для одного среза графа (конфиг с «вид»: «бенчмарк», например конфиги/физика_7.json)."""
    B = "бенчмарк/"
    has_calc = bool(cfg.get("ручные_данные")) and os.path.exists(A(cfg["ручные_данные"], "шаблоны_расчёт.py"))
    return [
        ("срез", B + "bench_slice.py", [], True, "граф → понятия, элементы, умения класса, веса по часам"),
        ("цок_корпус", B + "bench_corpus.py", [], True, "выгрузка ЦОК (цок_выгрузка.js) → текстовые единицы с уроком и ссылкой"),
        ("знания_кандидаты", B + "bench_candidates.py", [], True, "кандидаты-цитаты из корпуса для каждого понятия"),
        ("знания_llm", B + "llm_knowledge.py", [], True, "LLM наполняет понятия знаниями с дословными цитатами"),
        ("знания", B + "bench_knowledge.py", [], True, "проверка цитат → знания.jsonl"),
        ("формулы_llm", B + "llm_formulas.py", [], True, "LLM: формулы, обозначения, единицы класса"),
        ("задания_llm", B + "llm_items.py", [], True, "LLM по разделам: ошибки, ситуации, «почему», формулировки законов"),
        ("цепочки_роли_llm", B + "llm_chain_roles.py", [], True, "LLM: к какому объекту относится каждая величина формулы, условия, правдоподобные значения"),
        ("цепочки", B + "bench_chains.py", [], True, "кандидаты многошаговых задач из формул, глубина — по норме ФОП класса"),
        ("цепочки_llm", B + "llm_chains.py", [], True, "LLM-проверяющий: какие наборы формул дают осмысленную задачу (ок/нет)"),
        ("задания", B + "bench_tasks.py", [], True, "задания из графа, знаний, формул" + (", шаблонов расчёта" if has_calc else "")),
        ("дистракторы_llm", B + "llm_distractors.py", [], True, "LLM-проверяющий ищет спорные неверные варианты"),
        ("итог", B + "bench_tasks.py", ["--итог"], True, "пометки проверки, просмотр, автоматическая проверка набора"),
    ]


def stages(cfg, run):
    if cfg.get("вид") == "бенчмарк":
        return stages_bench(cfg)
    M, TMP, PH, G = A(run, "1_разметка"), A(run, "1_разметка", "промежуточное"), A(run, "1_разметка", "физика_поурочное"), A(run, "2_граф")
    none = A(TMP, "_без_правок.json")                       # несуществующий файл: алгоритм без правок
    phys = bool(cfg.get("физика_конструктор"))
    s = [
        # имя, скрипт, аргументы, нужен ли, что делает
        ("разметка", "разметка/fop_markup.py", [cfg["источник"]["pdf"], M], True, "PDF → records.jsonl, index.json, оглавление"),
        ("формулы", "разметка/mineru_map.py", [], bool(cfg.get("mineru")), "формулы физики из вывода MinerU"),
        ("уроки_история_авто", "разметка/align.py", [A(M, "records.jsonl"), none, A(TMP, "align_auto.json")], True, "уроки истории → элементы, алгоритм"),
        ("уроки_история_llm", "llm_lessons.py", ["history"], True, "LLM проверяет привязку уроков истории"),
        ("уроки_история", "разметка/align.py", [], True, "привязка уроков истории с решениями"),
        ("веса_история", "разметка/weights.py", [], True, "веса элементов истории по часам"),
        ("физика_конструктор", "физика/parse_constructor.py", [], phys, "уроки физики из PDF Конструктора"),
        ("уроки_физика_авто", "физика/build_physics.py", [none, A(PH, "_авто")], phys, "уроки физики → элементы, алгоритм"),
        ("уроки_физика_llm", "llm_lessons.py", ["physics"], phys, "LLM проверяет привязку уроков физики"),
        ("уроки_физика", "физика/build_physics.py", [], phys, "привязка уроков физики с решениями"),
        ("веса_физика", "физика/combine_weights.py", [], phys, "веса элементов физики (+ углублённый план, если задан)"),
        ("экзамен_авто", "разметка/exam_map.py", [none, A(TMP, "exam_auto.json")], True, "элемент → пункт ОГЭ/ЕГЭ, алгоритм"),
        ("экзамен_llm", "llm_exam.py", [], True, "LLM проверяет связь с экзаменом"),
        ("экзамен", "разметка/exam_map.py", [], True, "связь с экзаменом с решениями"),
        ("экзамен_связи", "разметка/exam_links.py", [], True, "прямые и обратные связи, часы за пунктом"),
        ("умения_авто", "разметка/req_map.py", [none, A(TMP, "req_auto.json")], True, "умение → требование экзамена, правила"),
        ("умения_llm", "llm_requirements.py", [], True, "LLM проверяет связь умений"),
        ("умения", "разметка/req_map.py", [], True, "связь умений с решениями"),
        ("умения_связи", "разметка/req_links.py", [], True, "обратные связи умений"),
        ("каркас", "граф/build_skeleton.py", [], True, "каркас графа из разметки"),
        ("понятия_llm", "llm_concepts.py", [], True, "LLM извлекает понятия"),
        ("синонимы_llm", "llm_synonyms.py", [], True, "LLM объединяет разные имена одного понятия"),
        ("понятия", "граф/build_concepts.py", [A(G, "понятия", "наборы")], True, "слой понятий"),
        ("проверка_понятий", "check:понятия", [], True, "evidence, типы, покрытие"),
        ("связи_llm", "llm_relations.py", [], True, "LLM ставит связи между понятиями"),
        ("проверка_связей", "check:связи", [], True, "id, типы, циклы, order_issue"),
        ("граф", "граф/build_graph.py", [], True, "весь граф + GraphML"),
        ("просмотр", "граф/build_viewer.py", [], True, "страница просмотра графа"),
    ]
    return s


def resolve(cfg):
    """Пути в конфиге — относительно папки автоматизация/."""
    def r(p):
        return p if (not p or os.path.isabs(p)) else os.path.normpath(A(AUTO, p))
    c = json.loads(json.dumps(cfg))
    if c.get("источник"):
        c["источник"]["pdf"] = r(c["источник"]["pdf"])
    for k in ("граф", "цок"):
        if c.get(k):
            c[k] = r(c[k])
    for k in ("физика_конструктор", "mineru"):
        if c.get(k):
            c[k] = {lvl: r(p) for lvl, p in c[k].items()}
    if c.get("ручные_данные"):
        c["ручные_данные"] = r(c["ручные_данные"])
    if c.get("replay"):
        c["replay"] = {k: r(p) for k, p in c["replay"].items()}
    if c.get("формулы_прошлых_классов"):
        c["формулы_прошлых_классов"] = [r(p) for p in c["формулы_прошлых_классов"]]
    if (c.get("llm") or {}).get("кэш"):
        c["llm"]["кэш"] = r(c["llm"]["кэш"])
    return c


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run_check(kind, run, env, log):
    d = A(run, "2_граф", "понятия" if kind == "понятия" else "связи", "наборы")
    script = A(ST, "граф", "check_concepts.py" if kind == "понятия" else "check_relations.py")
    ok = True
    for f in sorted(os.listdir(d)):
        if f.startswith("in_"):
            out = A(d, "out_" + f[3:])
            p = subprocess.run([sys.executable, script, A(d, f), out], env=env, capture_output=True, text=True)
            log.write(f"== {f}\n{p.stdout}{p.stderr}")
            first = (p.stdout.strip().split("\n") or [""])[0]
            print(f"    {f[3:-5]}: {first}")
            ok = ok and p.returncode == 0
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--run", help="имя папки прогона в прогоны/ (по умолчанию — имя конфига)")
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--only", help="этапы через запятую")
    ap.add_argument("--llm", choices=["api", "manual", "replay"], help="режим LLM вместо указанного в конфиге")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    cfg_path = a.config if os.path.isabs(a.config) else A(os.getcwd(), a.config)
    if not os.path.exists(cfg_path):
        cfg_path = A(AUTO, a.config)
    cfg = resolve(json.load(open(cfg_path, encoding="utf-8")))
    if a.llm:
        cfg.setdefault("llm", {})["режим"] = a.llm
    name = a.run or cfg.get("прогон") or os.path.splitext(os.path.basename(cfg_path))[0]
    run = A(AUTO, "прогоны", name)
    os.makedirs(A(run, "журнал"), exist_ok=True)
    S = stages(cfg, run)
    names = [x[0] for x in S]
    if a.list:
        for n, script, _, need, what in S:
            print(f"  {n:22s} {'' if need else '(пропуск по конфигу) '}{what}")
        return
    sel = names
    if a.only:
        sel = [x.strip() for x in a.only.split(",")]
    else:
        i = names.index(a.frm) if a.frm else 0
        j = names.index(a.to) if a.to else len(names) - 1
        sel = names[i:j + 1]
    bad = [x for x in sel if x not in names]
    if bad:
        sys.exit(f"нет этапов: {bad}. Список: python3 run.py {a.config} --list")

    json.dump(cfg, open(A(run, "конфиг.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    env = dict(os.environ, OZ_RUN=run, OZ_CONFIG=A(run, "конфиг.json"), OZ_MANUAL=cfg.get("ручные_данные") or "",
               PYTHONPATH=ST + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONIOENCODING="utf-8")
    for lvl, p in (cfg.get("mineru") or {}).items():
        env[f"OZ_MINERU_{lvl}"] = p
    for lvl, p in (cfg.get("физика_конструктор") or {}).items():
        env[f"OZ_CONSTRUCTOR_{lvl}"] = p

    man_path = A(run, "manifest.json")
    man = json.load(open(man_path, encoding="utf-8")) if os.path.exists(man_path) else {"этапы": {}}
    if cfg.get("вид") == "бенчмарк":
        src = A(cfg["граф"], "nodes.jsonl")
        src_info = {"граф": cfg["граф"], "sha256_nodes": sha256(src) if os.path.exists(src) else None,
                    "цок": cfg.get("цок"), "sha256_цок": sha256(cfg["цок"]) if cfg.get("цок") and os.path.exists(cfg["цок"]) else None}
    else:
        src = cfg["источник"]["pdf"]
        src_info = {"файл": src, "редакция": cfg["источник"].get("редакция"), "sha256": sha256(src) if os.path.exists(src) else None}
    man.update({"конфиг": os.path.relpath(cfg_path, AUTO), "источник": src_info,
                "llm": {k: (cfg.get("llm") or {}).get(k) for k in ("режим", "модель", "адрес")}})
    llm_mode = (cfg.get("llm") or {}).get("режим", "api")
    print(f"прогон: {run}\nисточник: {src}\nLLM: {llm_mode} {(cfg.get('llm') or {}).get('модель', '')}")

    for n, script, args, need, what in S:
        if n not in sel:
            continue
        if not need:
            print(f"— {n}: пропуск ({what})")
            if n == "формулы":
                os.makedirs(A(run, "1_разметка"), exist_ok=True)
                json.dump({}, open(A(run, "1_разметка", "формулы_mineru.json"), "w"))
            continue
        t0 = time.time()
        print(f"▶ {n}: {what}", flush=True)
        with open(A(run, "журнал", f"{names.index(n) + 1:02d}_{n}.log"), "w", encoding="utf-8") as log:
            if script.startswith("check:"):
                code = run_check(script.split(":")[1], run, env, log)
            else:
                path = A(ST, script)
                p = subprocess.run([sys.executable, path, *args], env=env, cwd=os.path.dirname(path),
                                   capture_output=True, text=True)
                log.write(p.stdout + p.stderr)
                tail = [l for l in (p.stdout + p.stderr).strip().split("\n") if l.strip()][-4:]
                for l in tail:
                    print("    " + l[:200])
                code = p.returncode
        if n == "просмотр" and code == 0:
            lib = A(run, "2_граф", "просмотр", "lib")
            if os.path.exists(lib):
                shutil.rmtree(lib)
            shutil.copytree(A(AUTO, "ресурсы", "просмотр_lib"), lib)
        man["этапы"][n] = {"код": code, "сек": round(time.time() - t0, 1), "время": time.strftime("%Y-%m-%d %H:%M:%S")}
        json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if code == 3:
            again = f"python3 run.py {a.config}" + (f" --run {a.run}" if a.run else "") + (f" --llm {a.llm}" if a.llm else "") + f" --from {n}"
            print(f"■ {n}: ждём ответов модели (режим manual). Запросы — {A(run, 'llm', 'запросы')}. После ответов: {again}")
            sys.exit(3)
        if code != 0:
            print(f"✖ {n}: ошибка, подробности — {A(run, 'журнал')}")
            sys.exit(code)
    print("готово:", run)


if __name__ == "__main__":
    main()
