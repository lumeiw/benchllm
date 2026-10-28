"""Обращение к LLM для этапов, где нужно суждение (раньше это делал эксперт или Claude в чате).

Режимы (config["llm"]["режим"]):
  api     — запрос к серверу с OpenAI-совместимым API: vLLM, llama.cpp server, Ollama, LM Studio, SGLang,
            а также облачные модели через совместимый прокси. Так подключается локальная Qwen.
  manual  — запросы пишутся файлами в <прогон>/llm/запросы/<этап>/; ответы (JSON) кладутся в
            <прогон>/llm/ответы/<этап>/ с тем же именем. Этап останавливается, пока ответов нет.
            Так можно отвечать руками или через чат с любой моделью.
  replay  — ответы берутся из прошлых результатов (старые out_*.json и *overrides.json).
            Нужен для проверки, что пайплайн воспроизводит текущие данные.

Каждый ответ проверяется функцией validate(obj) -> [ошибки]. Если ошибки есть, модели отправляется
повторный запрос с перечнем ошибок (до config["llm"]["повторов"] раз). Ответы кэшируются по хэшу
(модель + промпт + данные), поэтому повторный прогон спрашивает модель только о том, что изменилось.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
PROMPTS = os.path.join(AUTO, "промпты")


def load_config():
    return json.load(open(os.environ["OZ_CONFIG"], encoding="utf-8"))


SUBJECT_DIR = {"physics": "физика", "history": "история"}   # промпты бенчмарка предмета: промпты/<папка>/<name>.md


def prompt(name):
    """Текст промпта (без строк-комментариев <!-- -->). Для бенчмарка (в конфиге есть срез с предметом) сначала ищется
    промпты/<предмет>/<name>.md — промпты/физика/, промпты/история/; иначе (разметка и граф, части 1–2) — промпты/<name>.md."""
    path = os.path.join(PROMPTS, name + ".md")
    try:
        subj = (load_config().get("срез") or {}).get("предмет")
    except (KeyError, OSError, ValueError):
        subj = None
    if subj in SUBJECT_DIR:
        q = os.path.join(PROMPTS, SUBJECT_DIR[subj], name + ".md")
        if os.path.exists(q):
            path = q
    t = open(path, encoding="utf-8").read()
    return re.sub(r"<!--.*?-->\n?", "", t, flags=re.S).strip()


class NeedAnswers(Exception):
    """manual-режим: есть запросы без ответов."""


def parse_json(text):
    """JSON из ответа модели: убирает <think>…</think>, ```json-ограды, текст до/после."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("в ответе нет JSON")
    s = min(starts)
    end = max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[s:end + 1])


class LLM:
    def __init__(self, stage, replay=None):
        cfg = load_config()
        self.c = cfg.get("llm", {})
        self.stage = stage
        self.mode = self.c.get("режим", "api")
        self.replay = replay
        run = os.environ["OZ_RUN"]
        self.dir = os.path.join(run, "llm")
        self.req_dir = os.path.join(self.dir, "запросы", stage)
        self.ans_dir = os.path.join(self.dir, "ответы", stage)
        cache_root = self.c.get("кэш") or os.path.join(AUTO, "кэш_llm")
        self.cache_dir = os.path.join(cache_root if os.path.isabs(cache_root) else os.path.join(AUTO, cache_root), stage)
        for d in (self.dir, self.cache_dir):
            os.makedirs(d, exist_ok=True)
        self.log_path = os.path.join(self.dir, "журнал.jsonl")
        self.lock = threading.Lock()
        self.missing = []
        self.stats = dict(запросов=0, из_кэша=0, повторов=0, ошибок=0, replay=0)

    # ---------- общий вход ----------
    def ask(self, task_id, system, user, validate=None):
        """Вернуть объект-ответ на задачу task_id. user — строка или объект (будет сериализован в JSON)."""
        if not isinstance(user, str):
            user = json.dumps(user, ensure_ascii=False, indent=1)
        if self.mode == "replay":
            if self.replay is None:
                raise RuntimeError(f"{self.stage}: режим replay, но этап не дал прошлых ответов")
            obj = self.replay(task_id)
            self.stats["replay"] += 1
            return obj
        if self.mode == "manual":
            return self._manual(task_id, system, user, validate)
        return self._api(task_id, system, user, validate)

    def ask_many(self, tasks, validate_for=None):
        """tasks: [(task_id, system, user)]. Параллельно (config llm.параллельно). Возвращает {task_id: obj}."""
        out = {}
        par = int(self.c.get("параллельно", 1)) if self.mode == "api" else 1

        def one(t):
            tid, sysm, user = t
            v = validate_for(tid) if validate_for else None
            try:
                return tid, self.ask(tid, sysm, user, v)
            except NeedAnswers:
                return tid, None

        if par > 1:
            with ThreadPoolExecutor(par) as ex:
                for tid, obj in ex.map(one, tasks):
                    out[tid] = obj
        else:
            for t in tasks:
                tid, obj = one(t)
                out[tid] = obj
        if self.missing:
            raise NeedAnswers(f"{self.stage}: ждут ответа {len(self.missing)} запросов в {self.req_dir}; "
                              f"ответы (JSON) положите в {self.ans_dir} с тем же именем и запустите этап снова")
        return out

    # ---------- manual ----------
    def _manual(self, task_id, system, user, validate):
        os.makedirs(self.req_dir, exist_ok=True)
        os.makedirs(self.ans_dir, exist_ok=True)
        name = re.sub(r"[^\w.\-]+", "_", task_id)
        ans = os.path.join(self.ans_dir, name + ".json")
        if os.path.exists(ans):
            obj = parse_json(open(ans, encoding="utf-8").read())
            errs = validate(obj) if validate else []
            if not errs:
                return obj
            note = "\n\n## Ошибки в прошлом ответе — исправьте\n" + "\n".join("- " + e for e in errs[:40])
        else:
            note = ""
        with open(os.path.join(self.req_dir, name + ".md"), "w", encoding="utf-8") as f:
            f.write(f"# Система\n\n{system}\n\n# Запрос\n\n{user}{note}\n\n"
                    f"<!-- Ответ (только JSON) сохраните в {ans} -->\n")
        with self.lock:
            self.missing.append(task_id)
        raise NeedAnswers(task_id)

    # ---------- api ----------
    def _key(self, system, user):
        h = hashlib.sha256()
        for part in (self.c.get("модель", ""), str(self.c.get("температура", 0)), system, user):
            h.update(part.encode("utf-8")); h.update(b"\x00")
        return h.hexdigest()[:24]

    def _post(self, messages):
        url = self.c.get("адрес", "http://localhost:8000/v1").rstrip("/") + "/chat/completions"
        body = {"model": self.c.get("модель", ""), "messages": messages,
                "temperature": self.c.get("температура", 0), "max_tokens": self.c.get("макс_токенов", 8192)}
        if self.c.get("json_режим", True):
            body["response_format"] = {"type": "json_object"}
        body.update(self.c.get("доп_параметры") or {})          # напр. {"chat_template_kwargs": {"enable_thinking": false}}
        key = os.environ.get(self.c.get("ключ_из_переменной", "OZ_LLM_KEY"), "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.c.get("таймаут_сек", 600)) as r:
                    d = json.loads(r.read().decode("utf-8"))
                return d["choices"][0]["message"]["content"] or "", d.get("usage", {})
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"LLM недоступна по адресу {url}: {last}")

    def _api(self, task_id, system, user, validate):
        k = self._key(system, user)
        cpath = os.path.join(self.cache_dir, k + ".json")
        if os.path.exists(cpath):
            obj = json.load(open(cpath, encoding="utf-8"))["ответ"]
            if not validate or not validate(obj):
                with self.lock:
                    self.stats["из_кэша"] += 1
                return obj
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        tries = int(self.c.get("повторов", 2)) + 1
        errs, obj, t0 = [], None, time.time()
        for i in range(tries):
            content, usage = self._post(messages)
            with self.lock:
                self.stats["запросов"] += 1
                if i:
                    self.stats["повторов"] += 1
            try:
                obj = parse_json(content)
                errs = validate(obj) if validate else []
            except Exception as e:                      # noqa: BLE001 — любая ошибка разбора идёт в повтор
                obj, errs = None, [f"ответ не разобран как JSON: {e}"]
            self._log(task_id, i, usage, errs, time.time() - t0)
            if not errs:
                json.dump({"задача": task_id, "ответ": obj}, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
                return obj
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": "В ответе ошибки, исправь их и пришли ответ целиком заново (только JSON):\n"
                          + "\n".join("- " + e for e in errs[:40])}]
        with self.lock:
            self.stats["ошибок"] += 1
        raise RuntimeError(f"{self.stage}/{task_id}: после {tries} попыток ответ не прошёл проверку: {errs[:5]}")

    def _log(self, task_id, attempt, usage, errs, sec):
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"этап": self.stage, "задача": task_id, "попытка": attempt, "сек": round(sec, 1),
                                "токены": usage, "ошибок": len(errs), "ошибки": errs[:5],
                                "время": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")

    def report(self):
        print(f"[llm/{self.stage}] режим {self.mode}:", ", ".join(f"{k} {v}" for k, v in self.stats.items() if v))


def run_stage(main):
    """Обёртка для этапов: manual-режим без ответов → код выхода 3 (run.py сообщит, где лежат запросы)."""
    try:
        main()
    except NeedAnswers as e:
        print(str(e))
        sys.exit(3)


def chunks(seq, n):
    seq = list(seq)
    return [seq[i:i + n] for i in range(0, len(seq), n)]
