#!/usr/bin/env python3
"""Прогон заданий задания.jsonl через OpenRouter и автопроверка ответов. Промпты, проверка, судья и отчёт —
те же, что в gigachat_bench.py, поэтому результаты моделей сопоставимы.

  export OPENROUTER_API_KEY='<ключ>'                      # https://openrouter.ai/settings/keys
  python3 openrouter_bench.py --limit 20                  # пробный прогон обеих моделей
  python3 openrouter_bench.py                             # все задания, модели по умолчанию
  python3 openrouter_bench.py --models meta-llama/llama-3.1-8b-instruct --judge-model <модель>
  python3 openrouter_bench.py --report-only               # пересчитать итог по уже полученным ответам

Результаты — построчно в прогоны/<модель>.jsonl (/ в имени заменён на _); повторный запуск продолжает с места остановки.
Развёрнутые ответы оценивает судья; по умолчанию это та же модель (смещённо) — для честной оценки укажите
другую через --judge-model или пропустите их через --no-judge.
"""
import argparse, json, os, sys, time, threading
from concurrent.futures import ThreadPoolExecutor

import requests
from pathlib import Path

import gigachat_bench as gb

HERE = gb.HERE
URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = ["qwen/qwen3.7-flash", "meta-llama/llama-3.1-8b-instruct"]
EXTRA = {"qwen/qwen3.7-flash": {"reasoning": {"enabled": False}}}   # без рассуждений: ответ короткий и дешёвый


class Client:
    def __init__(self, key):
        self.key, self.lock, self.cost, self.tokens = key, threading.Lock(), 0.0, [0, 0]

    def chat(self, model, user, system=None, temperature=0.0, max_tokens=1500, retries=8):
        system = system or gb.SYSTEM
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "usage": {"include": True},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        body.update(EXTRA.get(model, {}))
        last = "?"
        for i in range(retries):
            wait = min(2 ** i, 60)
            try:
                r = requests.post(URL, json=body, timeout=180,
                                  headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                if r.status_code == 200:
                    j = r.json()
                    if "error" in j:                       # OpenRouter иногда отдаёт ошибку апстрима с кодом 200
                        last = str(j["error"])[:200]
                    else:
                        u = j.get("usage") or {}
                        with self.lock:
                            self.cost += u.get("cost") or 0
                            self.tokens[0] += u.get("prompt_tokens", 0); self.tokens[1] += u.get("completion_tokens", 0)
                        return j["choices"][0]["message"]["content"] or ""
                else:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code in (400, 401, 402, 403, 404):
                        raise RuntimeError(last)           # повтор не поможет
                    wait = max(wait, float(r.headers.get("Retry-After", 0) or 0))
            except requests.RequestException as e:
                last = repr(e)
            time.sleep(wait)
        raise RuntimeError(f"OpenRouter: исчерпаны попытки; последняя ошибка: {last}")


def fname(model):
    return model.replace("/", "_")


def run_model(cli, model, tasks, a):
    out_path = Path(a.tasks).resolve().parent / "прогоны" / f"{fname(model)}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    results = {}
    if a.fresh and out_path.exists():
        out_path.rename(out_path.with_name(f"{fname(model)}.{time.strftime('%Y%m%d-%H%M%S')}.old.jsonl"))
    if out_path.exists():
        for l in open(out_path, encoding="utf-8"):
            if l.strip():
                r = json.loads(l); results[r["id"]] = r
    if a.report_only:
        return results
    jm = a.judge_model or model
    todo = [t for t in tasks if t["id"] not in results]
    print(f"\n=== {model}: заданий {len(tasks)}, осталось {len(todo)}, судья {jm}")
    lock, done = threading.Lock(), [0]

    def work(t):
        try:
            raw = cli.chat(model, gb.build_prompt(t))
            score, ans = gb.check(t, raw)
            jtext = None
            if score is None and not a.no_judge:
                score, jtext = gb.judge(cli, jm, t, raw)
            rec = {"id": t["id"], "family": t["family"], "raw": raw, "extracted": ans,
                   "expected": t["answer"], "score": score, "judge": jtext}
        except Exception as e:
            print(f"! {t['id']}: {e}", file=sys.stderr)
            return
        with lock:
            results[t["id"]] = rec
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {done[0]}/{len(todo)}  ${cli.cost:.3f}")

    try:
        with ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(work, todo))
    except KeyboardInterrupt:
        print("\nПрервано; прогресс сохранён, запустите снова чтобы продолжить.")
        os._exit(0)
    print(f"  токены {cli.tokens[0]} вх. / {cli.tokens[1]} вых., стоимость ${cli.cost:.3f}")
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default=str(HERE / "задания.jsonl"))
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--judge-model", default=None, help="по умолчанию = оцениваемая модель")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="старый файл прогона убирается в архив, всё решается заново")
    a = ap.parse_args()

    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8") if l.strip()]
    if a.limit:
        step = max(len(tasks) // a.limit, 1)
        tasks = tasks[::step][:a.limit]
    gb.set_subject(tasks)
    key = os.getenv("OPENROUTER_API_KEY")
    if not key and not a.report_only:
        sys.exit("Задайте OPENROUTER_API_KEY.")
    for m in a.models:
        cli = Client(key)
        res = run_model(cli, m, tasks, a)
        print(f"--- {m}")
        gb.report(res, tasks)


if __name__ == "__main__":
    main()
