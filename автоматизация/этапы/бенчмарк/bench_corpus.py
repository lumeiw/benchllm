#!/usr/bin/env python3
"""Этап «цок_корпус»: выгрузка «Библиотеки ЦОК» → текстовые единицы с источником.

Вход: конфиг «цок» — файл, который сохраняет этапы/бенчмарк/цок_выгрузка.js (запускается в браузере).
Выход: 3_знания/<срез>/источники/цок_корпус.jsonl — по строке на единицу:
  {uid, lesson, lesson_name, lesson_url, task, eom, kind, text}
  kind: тест_ответ (вопрос → верный ответ), тест_решение, страница, интерактив, субтитры, цели_урока.
uid — хэш вида и текста (CK + 10 знаков): не меняется, если в выгрузке добавятся или пропадут другие материалы.
Тексты нормализуются (теги, пробелы), но не переписываются: цитата в знаниях — подстрока text.

«цок» может быть списком файлов (например, «История России» и «Всеобщая история» одного класса — разные каталоги ЦОК)
или null: тогда корпус пустой, и знания подтверждаются только цитатами из ФОП и кодификатора (их будет заметно меньше).
"""
import os
import hashlib
import html
import json
import re

from bench_common import C, jload, kn, write_jsonl

LESSON_URL = "https://academy-content.apkpro.ru/ru/lesson/{}"


def clean(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<textarea[^>]*/?>", " ", s)
    s = re.sub(r"</?[a-zA-Z][^>]*>", " ", s)
    s = s.replace("**", "")
    s = re.sub(r"\[изображение [^\]]+\]", " ", s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def flat(s):
    return re.sub(r"\s+", " ", clean(s)).strip()


def strings_of(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("eomType", "recommendationsForTeacher", "recommendationsForStudent", "number", "template", "vtt", "poster"):
                continue
            yield from strings_of(v, path + "." + k)
    elif isinstance(o, list):
        for v in o:
            yield from strings_of(v, path + "[]")
    elif isinstance(o, str) and re.search(r"[А-Яа-яЁё]", o):
        yield path, o


def load_exports():
    paths = C.get("цок")
    if not paths:
        return None
    paths = [paths] if isinstance(paths, str) else paths
    miss = [p for p in paths if not os.path.exists(p)]
    if miss:
        raise SystemExit("нет выгрузки ЦОК: " + "; ".join(miss) + "\nВыгрузите уроки скриптом этапы/бенчмарк/цок_выгрузка.js "
                         "(раздел «Новый класс» в README) или поставьте в конфиге \"цок\": null — тогда знания будут только из ФОП и кодификатора.")
    if len(paths) == 1:
        return jload(paths[0])
    lessons = []
    for p in paths:
        lessons += jload(p)["lessons"]
    return {"lessons": lessons}


def main():
    d = load_exports()
    if d is None:
        write_jsonl([], kn("источники/цок_корпус.jsonl"))
        print("ЦОК не задан (\"цок\": null): корпус пустой, знания подтверждаются только цитатами из ФОП и кодификатора")
        return
    units, seen = [], set()

    def add(lesson, task, kind, text):
        t = flat(text)
        if len(t) < 15 or (kind, t) in seen:
            return
        seen.add((kind, t))
        uid = "CK" + hashlib.sha1(f"{kind}\x00{t}".encode("utf-8")).hexdigest()[:10]
        units.append({"uid": uid, "lesson": lesson["number"], "lesson_name": lesson["name"], "lesson_url": LESSON_URL.format(lesson["uid"]),
                      "task": task.get("name") if task else None, "eom": task.get("eom") if task else None, "kind": kind, "text": t})

    for L in d["lessons"]:
        if L.get("youWillKnow") or L.get("youWillLearn"):
            add(L, None, "цели_урока", "Узнаете: " + "; ".join(L.get("youWillKnow") or []) + ". Научитесь: " + "; ".join(L.get("youWillLearn") or []))
        for T in L["tasks"]:
            for x in T["extracted"]:
                k = x.get("kind")
                if k == "test":
                    for task in x.get("tasks", []):
                        for v in task.get("variants", []):
                            q = flat(v.get("q", ""))
                            if v.get("sol"):
                                add(L, T, "тест_решение", v["sol"])
                            for el in v.get("els", []):
                                items = {i["id"]: flat(i.get("t", "")) for i in el.get("items", [])}
                                for key in el.get("keys", []):
                                    right = [items.get(s, "") for s in key.get("sel", []) if items.get(s)]
                                    right += [flat(t) for t in key.get("txt", []) if flat(t)]
                                    pairs = [f"{items.get(a, '')} — {items.get(b, '')}" for a, b in key.get("match", []) if items.get(a) and items.get(b)]
                                    if right and q:
                                        add(L, T, "тест_ответ", f"{q} → {' / '.join(right)}")
                                    if pairs and q:
                                        add(L, T, "тест_ответ", f"{q} → {'; '.join(pairs)}")
                elif k == "page":
                    for para in clean(x.get("text", "")).split("\n"):
                        add(L, T, "страница", para)
                elif k in ("structure", "data"):
                    for _, s in strings_of(x.get("data", {})):
                        for para in clean(s).split("\n"):
                            add(L, T, "интерактив", para)
                elif k == "subtitles":
                    add(L, T, "субтитры", x.get("text", ""))
    if len({u["uid"] for u in units}) != len(units):
        raise SystemExit("совпали uid разных единиц — увеличьте длину хэша")
    write_jsonl(units, kn("источники/цок_корпус.jsonl"))
    from collections import Counter
    print(f"уроков {len(d['lessons'])}, единиц {len(units)}:", dict(Counter(u["kind"] for u in units)))


if __name__ == "__main__":
    main()
