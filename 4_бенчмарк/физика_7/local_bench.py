#!/usr/bin/env python3
"""Прогон заданий на локальных моделях через llama.cpp (llama-server): все модели по очереди, без ручных действий.

Для каждой модели из local_models.json скрипт запускает llama-server, ждёт загрузки, прогоняет задания
через gigachat_bench.py (без судьи) и останавливает сервер. Потом запускает модель-судью и оценивает
развёрнутые ответы всех моделей (gigachat_bench.py --rejudge). В конце — сводная таблица (compare_runs.py).
Промпты, проверка ответов и файлы результатов — те же, что у прогона GigaChat, поэтому результаты сопоставимы.

  python local_bench.py --limit 20             # пробный прогон: по 20 заданий на модель
  python local_bench.py                        # все задания; уже решённые пропускаются
  python local_bench.py --only qwen3-8b-q8     # только эти модели (имена через запятую)
  python local_bench.py --no-judge             # без судьи
  python local_bench.py --judge-only           # только судья: ответы уже получены
  python local_bench.py --config local_models_files.json   # модели из заранее скачанных .gguf

llama-server ищется в PATH (после winget) или задаётся через --server / переменную LLAMA_SERVER,
например C:\\llama.cpp\\llama-server.exe. Журналы сервера — прогоны/логи/<имя>.log, версии llama.cpp и драйвера — прогоны/логи/среда.txt.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
BENCH = HERE / "gigachat_bench.py"
RUNS = HERE / "прогоны"
LOGS = RUNS / "логи"
BASE_ARGS = ["--no-mmproj"]          # картинки не нужны: не тратим видеопамять на проектор изображений
RUNNING = []                         # запущенный сейчас сервер — чтобы остановить его при любом выходе


def safe_name(model):                # как в gigachat_bench.py: имя файла прогона
    return re.sub(r"[^\w.\-]+", "_", model)


def save_env(server):
    """Версии llama.cpp и драйвера — в прогоны/логи/среда.txt (для описания условий эксперимента)."""
    LOGS.mkdir(parents=True, exist_ok=True)
    parts = ["==== " + time.strftime("%Y-%m-%d %H:%M")]
    for cmd in ([server, "--version"],
                ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
            parts.append("$ " + " ".join(cmd) + "\n" + (r.stdout + r.stderr).strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            parts.append("$ " + " ".join(cmd) + f"\n(не удалось: {e})")
    with open(LOGS / "среда.txt", "a", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n\n")


def alive(url):
    try:
        requests.get(url + "/health", timeout=2)
        return True
    except requests.RequestException:
        return False


def start(server, m, a):
    LOGS.mkdir(parents=True, exist_ok=True)
    src = ["-hf", m["hf"]] if m.get("hf") else ["-m", m["файл"]]
    cmd = [server, *src, "--alias", m["имя"], "-c", str(a.ctx), "-np", str(a.parallel),
           "--host", "127.0.0.1", "--port", str(a.port), *BASE_ARGS, *m.get("параметры", [])]
    print(f"\n=== {m['имя']}: запуск сервера\n    {' '.join(cmd)}", flush=True)
    log = open(LOGS / f"{safe_name(m['имя'])}.log", "wb")
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        log.close()
        sys.exit(f"не найден llama-server: «{server}». Укажите путь: --server C:\\llama.cpp\\llama-server.exe")
    RUNNING.append((proc, log))
    return proc, log


def wait_ready(proc, url, timeout):
    """Ждём, пока сервер загрузит модель (первый запуск с -hf ещё и скачивает её)."""
    t0, last = time.time(), 0
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            if requests.get(url + "/health", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        if time.time() - last > 60:
            print(f"    жду загрузку модели… {int(time.time() - t0)} с", flush=True)
            last = time.time()
        time.sleep(2)
    return False


def stop(proc, log):
    if (proc, log) in RUNNING:
        RUNNING.remove((proc, log))
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    log.close()


def log_tail(name, n=25):
    p = LOGS / f"{safe_name(name)}.log"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []
    return "\n".join("    | " + s for s in lines[-n:])


def solved_ids(name):
    f = RUNS / f"{safe_name(name)}.jsonl"
    if not f.exists():
        return set()
    return {json.loads(line)["id"] for line in open(f, encoding="utf-8") if line.strip()}


def need_judge(name, judge, rubric_ids, task_ids):
    """Есть ли у модели развёрнутые ответы, ещё не оценённые этим судьёй."""
    f = RUNS / f"{safe_name(name)}.jsonl"
    if not f.exists():
        return False
    recs = {}
    for line in open(f, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            recs[r["id"]] = r
    return any(r.get("score") is None or r.get("judge_model") != judge
               for tid, r in recs.items() if tid in rubric_ids and tid in task_ids)


def bench(*args):
    cmd = [sys.executable, str(BENCH), *args]
    print("    python gigachat_bench.py " + " ".join(args), flush=True)
    return subprocess.run(cmd, cwd=HERE).returncode


def main():
    ap = argparse.ArgumentParser(description="Прогон бенчмарка на локальных моделях через llama-server")
    ap.add_argument("--config", default=str(HERE / "local_models.json"))
    ap.add_argument("--server", default=os.getenv("LLAMA_SERVER", "llama-server"))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ctx", type=int, default=8192, help="контекст сервера (делится между параллельными запросами)")
    ap.add_argument("--parallel", type=int, default=2, help="параллельных запросов (-np у сервера и --workers у скрипта)")
    ap.add_argument("--limit", type=int, help="сколько заданий на модель (для пробы)")
    ap.add_argument("--only", help="имена моделей через запятую")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-only", action="store_true")
    ap.add_argument("--ready-timeout", type=int, default=3600, help="сколько ждать загрузки модели, с")
    a = ap.parse_args()

    cfg = json.load(open(a.config, encoding="utf-8"))
    models, judge = cfg["модели"], cfg.get("судья")
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        unknown = want - {m["имя"] for m in models}
        if unknown:
            sys.exit(f"нет в {Path(a.config).name}: {', '.join(sorted(unknown))}")
        models = [m for m in models if m["имя"] in want]
    entries = models + ([judge] if judge else [])
    names = [m["имя"] for m in entries]
    for m in entries:
        if not (m.get("hf") or m.get("файл")):
            sys.exit(f"у «{m['имя']}» нет ни «hf», ни «файл»")
        if m["имя"].startswith("GigaChat"):
            sys.exit(f"имя «{m['имя']}» начинается с «GigaChat»: gigachat_bench.py отправит проверку в облачный "
                     f"GigaChat. Назовите модель строчными буквами, например «{m['имя'].lower()}».")
    if len(set(names)) != len(names):
        sys.exit("имена моделей повторяются")
    missing = [m["файл"] for m in entries if not m.get("hf") and not Path(m["файл"]).is_file()]
    if missing:
        sys.exit("нет файлов моделей (скачайте их или поправьте путь в «файл»):\n  " + "\n  ".join(missing))

    url = f"http://127.0.0.1:{a.port}"
    if alive(url):
        sys.exit(f"на порту {a.port} уже работает сервер (LM Studio или другой llama-server). "
                 f"Закройте его или задайте другой порт: --port 8090")
    save_env(a.server)
    lim = ["--limit", str(a.limit)] if a.limit else []
    report = []
    tasks = [json.loads(l) for l in open(HERE / "задания.jsonl", encoding="utf-8") if l.strip()]
    if a.limit:                      # та же выборка, что делает gigachat_bench.py при --limit
        step = max(len(tasks) // a.limit, 1)
        tasks = tasks[::step][:a.limit]
    task_ids = {t["id"] for t in tasks}

    if not a.judge_only:
        for m in models:
            if not task_ids - solved_ids(m["имя"]):
                print(f"\n=== {m['имя']}: все задания уже решены — пропускаю")
                report.append((m["имя"], "все задания уже были решены"))
                continue
            t0 = time.time()
            proc, log = start(a.server, m, a)
            if not wait_ready(proc, url, a.ready_timeout):
                stop(proc, log)
                print(f"!!! {m['имя']}: сервер не запустился. Последние строки журнала:\n{log_tail(m['имя'])}")
                report.append((m["имя"], "сервер не запустился"))
                continue
            rc = bench("--base-url", url + "/v1", "--model", m["имя"], "--no-judge",
                       "--workers", str(a.parallel), *lim)
            stop(proc, log)
            report.append((m["имя"], f"ответы получены за {(time.time() - t0) / 60:.0f} мин" + ("" if rc == 0 else f", код {rc}")))

    if judge and not a.no_judge:
        rubric_ids = {t["id"] for t in tasks if t["check"]["type"] == "rubric"}
        todo = [m for m in models if need_judge(m["имя"], judge["имя"], rubric_ids, task_ids)]
        done = [m["имя"] for m in models if m not in todo and (RUNS / f"{safe_name(m['имя'])}.jsonl").exists()]
        if done:
            print(f"\nСудья {judge['имя']} уже оценил: {', '.join(done)}")
        if todo:
            t0 = time.time()
            proc, log = start(a.server, judge, a)
            if wait_ready(proc, url, a.ready_timeout):
                for m in todo:
                    print(f"\n=== судья {judge['имя']} → {m['имя']}", flush=True)
                    bench("--base-url", url + "/v1", "--model", m["имя"], "--judge-model", judge["имя"],
                          "--rejudge", "--workers", str(a.parallel), *lim)
                report.append((judge["имя"], f"судья: оценено моделей {len(todo)} за {(time.time() - t0) / 60:.0f} мин"))
            else:
                print(f"!!! судья {judge['имя']}: сервер не запустился. Последние строки журнала:\n{log_tail(judge['имя'])}")
                report.append((judge["имя"], "судья не запустился"))
            stop(proc, log)

    print("\n=== итог запуска")
    for name, what in report:
        print(f"  {name}: {what}")
    subprocess.run([sys.executable, str(HERE / "compare_runs.py")], cwd=HERE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано. Ответы сохранены; повторный запуск продолжит с места остановки.")
    finally:
        for proc, log in list(RUNNING):
            stop(proc, log)
