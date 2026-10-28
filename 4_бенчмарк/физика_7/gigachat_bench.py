#!/usr/bin/env python3
"""Прогон заданий задания.jsonl через GigaChat API или любой OpenAI-совместимый API и автопроверка ответов.

OpenAI-совместимый режим (--base-url): OpenRouter, vLLM/Ollama в Colab или на сервере, YandexGPT и др.
Ключ берётся из env, имя переменной — --api-key-env (по умолчанию OPENAI_API_KEY; для Ollama/vLLM можно любой).

  export OPENROUTER_API_KEY='<ключ>'
  python3 gigachat_bench.py --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY \
      --model qwen/qwen-2.5-7b-instruct --judge-model GigaChat-2-Max --limit 100
  python3 gigachat_bench.py --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-7B-Instruct --no-judge

Судья по умолчанию — та же модель. Если --judge-model начинается с «GigaChat», судья идёт через GigaChat
(нужен GIGACHAT_CREDENTIALS) — так слабую модель проверяет сильная, а не она сама себя.

Ключ авторизации (Authorization key из Studio -> Настройки API) берётся из env GIGACHAT_CREDENTIALS.

  export GIGACHAT_CREDENTIALS='<ключ>'
  python3 gigachat_bench.py --limit 20                    # пробный прогон
  python3 gigachat_bench.py                               # все задания, модель GigaChat-2-Max
  python3 gigachat_bench.py --report-only                 # пересчитать итог по уже полученным ответам

Результаты пишутся построчно в прогоны/<модель>.jsonl; повторный запуск пропускает уже решённые задания.
Развёрнутые (rubric) оценивает судья — по умолчанию та же модель (--judge-model), это смещённая оценка;
для честной лучше указать другую модель или проверить вручную (--no-judge пропускает их).
"""
import argparse, json, os, re, sys, time, uuid, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).parent
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

SYSTEM = "Ты решаешь задания по физике за 7 класс. Следуй формату ответа из задания строго."
SUBJ = {"физика": ("физике", "физики", "физическая"), "история": ("истории", "истории", "фактическая")}
ERR_WORD = "физическая"


def set_subject(tasks):
    """Предмет и класс — из id заданий (T.<предмет>_<класс>.…): промпт модели и судьи под срез, а не только «физика, 7 класс»."""
    global SYSTEM, JUDGE_SYS, ERR_WORD
    m = re.match(r"T\.([а-яё]+)_(\d+)\.", tasks[0]["id"]) if tasks else None
    if not m or m.group(1) not in SUBJ:
        return
    po, uch, ERR_WORD = SUBJ[m.group(1)]
    SYSTEM = f"Ты решаешь задания по {po} за {m.group(2)} класс. Следуй формату ответа из задания строго."
    JUDGE_SYS = f"Ты опытный учитель {uch}, проверяющий ответы учеников по эталону. Оцениваешь по смыслу, а не по словам."
FORMAT_HINT = {
    "один_ответ": "Выбери ровно один вариант. В последней строке напиши: «Ответ: <номер варианта>».",
    "несколько_ответов": "Выбери все верные варианты. В последней строке напиши: «Ответ: <номера подряд без пробелов, по возрастанию, например 134>».",
    "порядок": "В последней строке напиши: «Ответ: <номера вариантов в нужном порядке подряд, например 3214>».",
    "число": "В последней строке напиши: «Ответ: <число>» (только число, без единиц измерения; десятичная запятая или точка).",
    "число_с_погрешностью": "В последней строке напиши: «Ответ: <значение> ± <погрешность>» (числа без единиц).",
    "текст": "В последней строке напиши: «Ответ: <одно слово или название>».",
    "развёрнутый": "Ответь кратко и по существу (2–5 предложений).",
}


class Client:
    def __init__(self, credentials, scope, verify):
        self.cred, self.scope, self.verify = credentials, scope, verify
        self.token, self.exp, self.lock = None, 0, threading.Lock()

    def _token(self):
        with self.lock:
            if self.token and time.time() < self.exp - 60:
                return self.token
            r = requests.post(
                OAUTH_URL,
                headers={"Authorization": f"Basic {self.cred}", "RqUID": str(uuid.uuid4()),
                         "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                data={"scope": self.scope}, verify=self.verify, timeout=30)
            r.raise_for_status()
            j = r.json()
            self.token, self.exp = j["access_token"], j["expires_at"] / 1000
            return self.token

    def chat(self, model, user, system=None, temperature=0.0, max_tokens=1500, retries=8):
        system = system or SYSTEM
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        last = "?"
        for i in range(retries):
            wait = min(2 ** i, 60)
            try:
                r = requests.post(API_URL, json=body, verify=self.verify, timeout=120,
                                  headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"})
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code == 401:
                    self.token, wait = None, 0
                elif r.status_code in (429, 500, 502, 503, 504):
                    wait = max(wait, float(r.headers.get("Retry-After", 0) or 0))
                else:
                    raise RuntimeError(last)  # 400/402/403 и т.п. — повтор не поможет
            except requests.RequestException as e:
                last = repr(e)
            time.sleep(wait)
        raise RuntimeError(f"GigaChat: исчерпаны попытки; последняя ошибка: {last}")


class OpenAIClient:
    """Любой OpenAI-совместимый /chat/completions."""
    def __init__(self, base_url, api_key):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key

    def chat(self, model, user, system=None, temperature=0.0, max_tokens=1500, retries=8):
        system = system or SYSTEM
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        last = "?"
        for i in range(retries):
            wait = min(2 ** i, 60)
            try:
                r = requests.post(self.url, json=body, timeout=300,
                                  headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                if r.status_code == 200:
                    j = r.json()
                    if "choices" in j and j["choices"]:
                        return j["choices"][0]["message"].get("content") or ""
                    last = f"нет choices: {str(j)[:200]}"
                else:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code in (400, 401, 402, 403, 404):
                        raise RuntimeError(last)
                    wait = max(wait, float(r.headers.get("Retry-After", 0) or 0))
            except requests.RequestException as e:
                last = repr(e)
            time.sleep(wait)
        raise RuntimeError(f"API: исчерпаны попытки; последняя ошибка: {last}")


def safe_name(model):
    return re.sub(r"[^\w.\-]+", "_", model)


def build_prompt(t):
    p = t["question"].strip()
    if t.get("options"):
        p += "\n\nВарианты:\n" + "\n".join(f"{o['key']}. {o['text']}" for o in t["options"])
    return p + "\n\n" + FORMAT_HINT.get(t["format"], "")


# ---------- разбор и проверка ----------
def extract(text):
    m = re.findall(r"Ответ\s*[:：]\s*(.+)", text, flags=re.I)
    return (m[-1] if m else text.strip().splitlines()[-1] if text.strip() else "").strip().strip("*. ")


def num(s):
    m = re.search(r"-?\d[\d\s]*(?:[.,]\d+)?(?:\s*[·x×*]\s*10\s*\^?\s*[-−]?\d+)?", s.replace(" ", " "))
    if not m:
        return None
    x = m.group(0)
    e = re.search(r"[·x×*]\s*10\s*\^?\s*([-−]?\d+)", x)
    mant = re.sub(r"\s*[·x×*]\s*10.*", "", x)
    mant = mant.replace(" ", "").replace(",", ".")
    try:
        v = float(mant)
    except ValueError:
        return None
    if e:
        v *= 10 ** int(e.group(1).replace("−", "-"))
    return v


def digits(s):
    return "".join(re.findall(r"\d", s))


def check(t, raw):
    """-> (баллы 0..1, извлечённый ответ) или (None, ...) для rubric."""
    c, ans = t["check"], extract(raw)
    ty = c["type"]
    if ty == "choice":
        return float(sorted(set(digits(ans))) == sorted(set(c["value"]))), ans
    if ty == "sequence":
        return float(digits(ans) == c["value"]), ans
    if ty == "number":
        v = num(ans)
        if v is None:
            return 0.0, ans
        ref, tol = float(c["value"]), c.get("rel_tol", 0.01)
        return float(abs(v - ref) <= tol * abs(ref) + 1e-12), ans
    if ty == "value_error":
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", ans)]
        ok = len(nums) >= 2 and abs(nums[0] - c["value"]) < 1e-6 and abs(nums[1] - c["error"]) < 1e-6
        return float(ok), ans
    if ty == "text":
        a = ans.lower().replace("ё", "е")
        return float(any(x.lower().replace("ё", "е") in a for x in c["accept"])), ans
    return None, ans


JUDGE_SYS = "Ты опытный учитель физики, проверяющий ответы учеников по эталону. Оцениваешь по смыслу, а не по словам."


def judge(cli, model, t, raw):
    """-> (доля баллов 0..1 или None, текст рассуждения судьи)."""
    c = t["check"]
    mx = c.get("max", 1)
    p = (f"Вопрос: {t['question']}\n\nЭталон: {c.get('reference')}\n\nКритерии: {c.get('criteria')}\n"
         f"Максимум баллов: {mx}\n\nОтвет ученика:\n{raw}\n\n"
         "Правила проверки:\n"
         "- Засчитывай ответ, если он по смыслу верно передаёт ГЛАВНУЮ мысль эталона; перефразирование, другие слова и "
         "дополнительные верные сведения не вредят.\n"
         "- Не снижай балл за детали эталона, которые не нужны для ответа на сам вопрос.\n"
         f"- Снижай балл, если главная мысль пропущена, искажена или в ответе есть {ERR_WORD} ошибка.\n\n"
         "Сначала по шагам: 1) выпиши главные пункты эталона; 2) для каждого напиши, есть ли он в ответе; "
         "3) отметь ошибки. Затем в последней строке: «Балл: <число от 0 до максимума>».")
    out = cli.chat(model, p, system=JUDGE_SYS, max_tokens=900)
    m = re.findall(r"Балл\s*[:：]\s*\**\s*(\d+(?:[.,]\d+)?)", out)
    if not m:
        return None, out
    return min(float(m[-1].replace(",", ".")) / mx, 1.0), out


# ---------- основной цикл ----------
def report(results, tasks):
    by = {t["id"]: t for t in tasks}
    groups = {}
    tot = [0.0, 0.0, 0, 0]  # score, weight, n, nscored
    for r in results.values():
        t = by.get(r["id"])
        if not t:
            continue
        for key in ("all", f"формат: {t['format']}", f"семейство: {t['family']}", f"раздел: {t['section']}"):
            g = groups.setdefault(key, [0.0, 0.0, 0, 0, 0.0])
            g[2] += 1
            if r["score"] is not None:
                g[0] += r["score"]; g[3] += 1; g[4] += r["score"] * t["weight"]; g[1] += t["weight"]
    print(f"\n{'группа':32} {'решено':>7} {'оценено':>8} {'точность':>9} {'взвеш.':>8}")
    for k in sorted(groups, key=lambda k: (k != "all", k)):
        s, w, n, ns, ws = groups[k]
        if ns:
            print(f"{k:32} {n:7} {ns:8} {s/ns:9.1%} {ws/w:8.1%}")
    pending = sum(1 for r in results.values() if r["score"] is None)
    if pending:
        print(f"\nБез оценки (rubric без судьи / ошибка судьи): {pending}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(HERE / "задания.jsonl"))
    ap.add_argument("--model", default="GigaChat-2-Max")
    ap.add_argument("--judge-model", default=None, help="по умолчанию = --model; GigaChat-* — судья через GigaChat")
    ap.add_argument("--base-url", default=None, help="OpenAI-совместимый API вместо GigaChat")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY", help="имя переменной окружения с ключом для --base-url")
    ap.add_argument("--scope", default=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="полное ретестирование: старый файл прогона убирается в архив, всё решается заново")
    ap.add_argument("--rejudge", action="store_true",
                    help="заново оценить развёрнутые ответы из файла прогона (ответы модели не пересчитываются)")
    ap.add_argument("--ca-bundle", default=os.getenv("GIGACHAT_CA_BUNDLE"),
                    help="путь к сертификату Минцифры; без него — отключаем проверку TLS (небезопасно)")
    a = ap.parse_args()

    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8") if l.strip()]
    if a.limit:
        # равномерная выборка по порядку, а не первые N одного семейства
        step = max(len(tasks) // a.limit, 1)
        tasks = tasks[::step][:a.limit]
    set_subject(tasks)

    out_path = Path(a.tasks).resolve().parent / "прогоны" / f"{safe_name(a.model)}.jsonl"   # прогоны — рядом с заданиями среза
    out_path.parent.mkdir(exist_ok=True)
    results = {}
    if a.fresh and out_path.exists():
        arch = out_path.with_name(f"{safe_name(a.model)}.{time.strftime('%Y%m%d-%H%M%S')}.old.jsonl")
        out_path.rename(arch)
        print(f"Старый прогон сохранён: {arch.name}")
    if out_path.exists():
        for l in open(out_path, encoding="utf-8"):
            if l.strip():
                r = json.loads(l); results[r["id"]] = r
    if a.report_only:
        return report(results, tasks)

    def giga():
        cred = os.getenv("GIGACHAT_CREDENTIALS")
        if not cred:
            sys.exit("Задайте GIGACHAT_CREDENTIALS (Authorization key из Studio).")
        verify = a.ca_bundle or False
        if not a.ca_bundle:
            import urllib3; urllib3.disable_warnings()
            print("⚠ TLS-проверка отключена; лучше задать --ca-bundle с сертификатом Минцифры", file=sys.stderr)
        return Client(cred, a.scope, verify)

    if a.base_url:
        cli = OpenAIClient(a.base_url, os.getenv(a.api_key_env, "none"))
    else:
        cli = giga()
    jm = a.judge_model or a.model
    jcli = giga() if (a.base_url and jm.startswith("GigaChat")) else cli

    if a.rejudge:
        by = {t["id"]: t for t in tasks}
        rub = [r for r in results.values() if r["id"] in by and by[r["id"]]["check"]["type"] == "rubric"]
        print(f"Переоценка развёрнутых: {len(rub)}, судья: {jm}")

        def rj(r):
            try:
                r["score"], r["judge"] = judge(jcli, jm, by[r["id"]], r["raw"])
                r["judge_model"] = jm
            except Exception as e:
                print(f"! {r['id']}: {e}", file=sys.stderr)
        try:
            with ThreadPoolExecutor(a.workers) as ex:
                list(ex.map(rj, rub))
        except KeyboardInterrupt:
            os._exit(0)
        with open(out_path, "w", encoding="utf-8") as f:   # переписываем файл целиком
            for r in results.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return report(results, tasks)

    todo = [t for t in tasks if t["id"] not in results]
    print(f"Заданий: {len(tasks)}, осталось: {len(todo)}, модель: {a.model}")
    lock, done = threading.Lock(), [0]

    def work(t):
        try:
            raw = cli.chat(a.model, build_prompt(t))
            score, ans = check(t, raw)
            jtext = None
            if score is None and not a.no_judge:
                score, jtext = judge(jcli, jm, t, raw)
            rec = {"id": t["id"], "family": t["family"], "raw": raw, "extracted": ans,
                   "expected": t["answer"], "score": score, "judge": jtext, "model": a.model,
                   "judge_model": jm if jtext else None}
        except Exception as e:
            print(f"! {t['id']}: {e}", file=sys.stderr)
            return
        with lock:
            results[t["id"]] = rec
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {done[0]}/{len(todo)}")

    try:
        with ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(work, todo))
    except KeyboardInterrupt:
        print("\nПрервано; прогресс сохранён, запустите снова чтобы продолжить.")
        os._exit(0)
    report(results, tasks)


if __name__ == "__main__":
    main()
