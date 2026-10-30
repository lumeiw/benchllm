"""Общее для этапов бенчмарка (части 3–4: знания и задания).

Пути одного прогона бенчмарка (их задаёт run.py через OZ_RUN и OZ_CONFIG):
  <прогон>/3_знания/<срез>/   срез.json, источники/цок_корпус.jsonl, кандидаты.json, знания.jsonl, формулы.json
  <прогон>/4_бенчмарк/<срез>/ задания.jsonl, сводка.json, задания.md, просмотр.html
  <прогон>/решения/           решения LLM: знания.json, формулы.json, задания_llm.json, дистракторы.json

Конфиг бенчмарка — конфиги/<срез>.json, «вид»: «бенчмарк» (пример — конфиги/физика_7.json).
"""
import hashlib
import importlib.util
import json
import os
import random
import re

RUN = os.environ["OZ_RUN"]


def cfg():
    return json.load(open(os.environ["OZ_CONFIG"], encoding="utf-8"))


C = cfg()
SLICE = C["срез"]["имя"]
KN = os.path.join(RUN, "3_знания", SLICE)
BN = os.path.join(RUN, "4_бенчмарк", SLICE)
DEC = os.path.join(RUN, "решения")
for _d in (KN, os.path.join(KN, "источники"), BN, DEC):
    os.makedirs(_d, exist_ok=True)
GRAPH = C["граф"]
MANUAL = C.get("ручные_данные") or ""


def kn(name):
    return os.path.join(KN, name)


def bn(name):
    return os.path.join(BN, name)


def dec(name):
    return os.path.join(DEC, name)


SUBJECT_RU = {"physics": "Физика", "history": "История", "chemistry": "Химия", "biology": "Биология", "math": "Математика"}


def slice_title():
    """Название среза для людей: «название» из конфига или «Физика, 7 класс»."""
    s = C["срез"]
    if s.get("название"):
        return s["название"]
    g = s.get("классы") or []
    grades = f"{g[0]} класс" if len(g) == 1 else "классы " + ", ".join(map(str, g))
    return f"{SUBJECT_RU.get(s.get('предмет'), s.get('предмет', ''))}, {grades}"


def manual(name):
    p = os.path.join(MANUAL, name) if MANUAL else ""
    return p if p and os.path.exists(p) else None


def jl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def jload(path):
    return json.load(open(path, encoding="utf-8"))


def jdump(obj, path, indent=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=indent)


def write_jsonl(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def norm(s):
    """Сравнение цитат: схлопнутые пробелы, без учёта регистра «ё/е» не трогаем — цитата должна быть дословной."""
    return re.sub(r"\s+", " ", s or "").strip()


def rng(key):
    return random.Random(int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], 16))


def stems(label):
    return {w[:5] for w in re.findall(r"[а-яё]+", label.lower()) if len(w) >= 5}


def slug(cid):
    return cid.split(".")[-1]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def mode():
    return (C.get("llm") or {}).get("режим", "api")


def setting(key, default):
    return (C.get("llm") or {}).get(key, default)


def replay_path(key):
    p = (C.get("replay") or {}).get(key)
    return p if p and os.path.exists(p) else None


# ───── умения ФОП по ролям: код умения у разных классов разный, роль — одна ─────
ROLES = {
    # 7–9 кл. (ООО) и 10–11 кл. (СОО): у СОО формулировки умений другие, поэтому у ролей есть вторые варианты
    "понятия": r"^использовать (изученные )?понятия",          # в 10 кл. такого умения нет: код по разделу — ручные_данные/…/умения.json
    "различать": r"^(различать явления|распознавать физические явления)",
    "распознавать": r"^(распознавать проявлени|распознавать физические явления)",
    "описывать": r"^описывать",                                  # в 10 кл. три умения (механика, тепловые, электрические) — по разделу
    "характеризовать": r"^(характеризовать свойства|анализировать физические процессы)",
    "объяснять": r"^(объяснять (?!(основные )?принципы)|решать качественные задачи)",
    "решать": r"^решать расч",
    "гипотеза": r"^(распознавать проблемы|выполнять эксперименты)",
    "опыты": r"^(проводить опыты|выполнять эксперименты)",
    "прямые": r"^(выполнять прямые измерения|проводить при необходимости серию прямых|осуществлять прямые и косвенные)",
    "исследование": r"^(проводить исследование|исследовать зависимости)",
    "косвенные": r"^(проводить косвенные|осуществлять прямые и косвенные)",
    "тб": r"^соблюдать правила",
    "приборы": r"^(указывать принципы|характеризовать принципы|объяснять (основные )?принципы действия)",   # 11 кл. 11.16: «объяснять принципы действия машин»
    "примеры": r"^(приводить примеры (?!вклада)|использовать теоретические знания)",
    "источники": r"^(осуществлять (отбор|поиск)|использовать при решении учебных задач современные)",
    "знаковые": r"^(использовать при выполнении|использовать при решении учебных задач современные)",
    "схемы": r"^(распознавать простые технические устройства|использовать схемы)",   # 8 кл. 1.15, 9 кл. 1.16: схемы устройств, условные обозначения, оптические схемы
    "модели": r"^(различать основные признаки изученных физических моделей|учитывать границы применения изученных физических моделей)",   # 9 кл. 1.14, 10 кл. 10.2
    "учёные": r"^приводить примеры вклада",                      # 10 кл. 10.16: вклад российских и зарубежных учёных
    "картина": r"^демонстрировать на примерах роль",             # 10 кл. 10.1: роль физики в научной картине мира
    "направление": r"^определять направлени",                    # 11 кл. 11.7: направление индукции, силы Ампера и силы Лоренца
    "изображение": r"^строить и описывать изображени",           # 11 кл. 11.8: изображение в плоском зеркале и тонкой линзе
}


def resolve_role(requirements, role_or_code):
    """Код умения ФОП класса по роли («объяснять») или проверенный код («1.6»). None — если такого умения нет."""
    codes = {r["code"] for r in requirements}
    if role_or_code in codes:
        return role_or_code
    pat = ROLES.get(role_or_code)
    if not pat:
        return None
    for r in requirements:
        if re.search(pat, r["text"].strip().lower()):
            return r["code"]
    return None


# ───── срез и граф ─────
class Slice:
    """срез.json + связи между его понятиями + отношение «связаны» для подбора неверных вариантов."""

    def __init__(self, path=None):
        self.s = jload(path or kn("срез.json"))
        self.C = {c["id"]: c for c in self.s["concepts"]}
        self.L = {c["label"]: c for c in self.s["concepts"]}
        self.req = {r["code"]: r for r in self.s["requirements"]}
        self.adj, self.edges = {}, {}
        for r in self.s["relations"]:
            self.edges.setdefault(r["type"], []).append(r)
            self.adj.setdefault(r["source"], set()).add(r["target"])
            self.adj.setdefault(r["target"], set()).add(r["source"])
        up = {}
        for t in ("is_a", "part_of"):
            for r in self.edges.get(t, []):
                up.setdefault(r["source"], set()).add(r["target"])
        self.anc = {c: self._closure(c, up) for c in self.C}
        self.desc = {c: set() for c in self.C}
        for c, a in self.anc.items():
            for x in a:
                self.desc.setdefault(x, set()).add(c)
        self.pre, self.succ = {}, {}
        for r in self.edges.get("prerequisites_for", []):
            self.pre.setdefault(r["target"], set()).add(r["source"])
            self.succ.setdefault(r["source"], set()).add(r["target"])
        self.pre_all = {c: self._closure(c, self.pre) for c in self.C}
        self.succ_all = {c: self._closure(c, self.succ) for c in self.C}
        self.similar = []
        self.formula_groups = []

    @staticmethod
    def _closure(start, nxt):
        seen, st = set(), [start]
        while st:
            x = st.pop()
            for y in nxt.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    st.append(y)
        return seen

    def lab(self, cid):
        return self.C[cid]["label"]

    def related(self, cid):
        """Понятия, которые нельзя ставить неверным вариантом к cid: соседи по связям, роды и виды, похожие названия,
        общая формула, ручные «близкие пары»."""
        out = {cid} | self.adj.get(cid, set()) | self.anc.get(cid, set()) | self.desc.get(cid, set())
        lab = self.lab(cid).lower()
        for x, cx in self.C.items():
            lx = cx["label"].lower()
            if lab in lx or lx in lab:
                out.add(x)
        for grp in self.formula_groups:
            if self.lab(cid) in grp:
                out |= {self.L[c]["id"] for c in grp if c in self.L}
        for a, b in self.similar:
            if self.lab(cid) in (a, b):
                out |= {self.L[c]["id"] for c in (a, b) if c in self.L}
        return out

    def also_correct(self, cid):
        """Узкое правило для неверных вариантов, которые пишет модель к ситуации: исключаются только понятия, которые
        в вопросе «что здесь проявляется?» тоже были бы верным ответом — роды и виды, части и целое, понятия, чьё название
        целиком входит в название ответа (или наоборот, по словам), ручные «близкие пары». Соседние понятия одного рода
        (упругая и неупругая деформация) остаются: ситуация как раз должна их различать."""
        out = {cid} | self.anc.get(cid, set()) | self.desc.get(cid, set())
        words = set(re.findall(r"\w+", self.lab(cid).lower()))
        for x, cx in self.C.items():
            wx = set(re.findall(r"\w+", cx["label"].lower()))
            if words <= wx or wx <= words:
                out.add(x)
        for a, b in self.similar:
            if self.lab(cid) in (a, b):
                out |= {self.L[c]["id"] for c in (a, b) if c in self.L}
        return out


def load_slice_full():
    """Срез + ручные «близкие пары» + группы понятий с общей формулой (если справочник формул уже есть)."""
    sl = Slice()
    p = manual("близкие_пары.json")
    sl.similar = [tuple(x) for x in jload(p)] if p else []
    if os.path.exists(kn("формулы.json")):
        sl.formula_groups = [f["concepts"] for f in jload(kn("формулы.json")).get("формулы", [])]
    return sl
