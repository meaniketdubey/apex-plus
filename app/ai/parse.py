"""Turn free text ("3x10 squats at 80kg, bench 80x8 85x8") into structured sets.

Two parsers feed the same matcher:
  * an LLM parser (when an API key is configured), and
  * a regex parser (always available, also the fallback when the LLM fails).

Nothing here writes to the database. The app shows the result and the user confirms it.
"""
import difflib
import json
import re
from dataclasses import dataclass, field

from .llm import LLM, LLMError

LB_TO_KG = 0.45359237


@dataclass
class ParsedSet:
    weight_kg: float
    reps: int


@dataclass
class ParsedItem:
    text: str  # what the user called the exercise
    sets: list[ParsedSet] = field(default_factory=list)


# ------------------------------------------------------------- matching
ALIASES = {
    "bench": "barbell bench press",
    "bench press": "barbell bench press",
    "flat bench": "barbell bench press",
    "incline bench": "incline barbell bench press",
    "incline press": "incline barbell bench press",
    "squat": "barbell back squat",
    "squats": "barbell back squat",
    "back squat": "barbell back squat",
    "rdl": "romanian deadlift",
    "deadlifts": "deadlift",
    "ohp": "overhead press",
    "shoulder press": "overhead press",
    "military press": "overhead press",
    "pullup": "pull-up",
    "pullups": "pull-up",
    "pull up": "pull-up",
    "pull ups": "pull-up",
    "pushup": "push-up",
    "pushups": "push-up",
    "push up": "push-up",
    "push ups": "push-up",
    "dips": "weighted dip",
    "dip": "weighted dip",
    "row": "barbell row",
    "rows": "barbell row",
    "lat pulldowns": "lat pulldown",
    "curl": "barbell curl",
    "curls": "barbell curl",
    "bicep curl": "barbell curl",
    "tricep pushdown": "triceps pushdown",
    "pushdown": "triceps pushdown",
    "calf raise": "standing calf raise",
    "calf raises": "standing calf raise",
    "lateral raise": "cable lateral raise",
    "lateral raises": "cable lateral raise",
    "leg curls": "leg curl",
    "split squat": "bulgarian split squat",
    "split squats": "bulgarian split squat",
    "leg raise": "hanging leg raise",
    "leg raises": "hanging leg raise",
    "flys": "dumbbell fly",
    "flyes": "dumbbell fly",
    "fly": "dumbbell fly",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\- ]", " ", s.lower())).strip()


def match_exercise(text: str, exercises: list) -> object | None:
    """Best library exercise for a free-text name, or None. `exercises` need .name."""
    q = _norm(text)
    if not q:
        return None
    q = ALIASES.get(q, q)
    by_norm = {_norm(e.name): e for e in exercises}
    if q in by_norm:
        return by_norm[q]
    # every word of the query appears in an exercise name ("incline bench" -> "Incline Barbell Bench Press")
    words = set(q.replace("-", " ").split())
    hits = [e for n, e in by_norm.items() if words and words <= set(n.replace("-", " ").split())]
    if len(hits) == 1:
        return hits[0]
    if hits:  # several: prefer the shortest name (most generic)
        return min(hits, key=lambda e: len(e.name))
    close = difflib.get_close_matches(q, list(by_norm), n=1, cutoff=0.72)
    return by_norm[close[0]] if close else None


# ---------------------------------------------------------- rule parser
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(kgs?|kilos?|lbs?|pounds?)"
_W_UNIT = re.compile(rf"{_NUM}\s*{_UNIT}\b", re.I)
_W_AT = re.compile(rf"(?:\bat|@)\s*{_NUM}(?!\s*(?:reps?|x|×|sets?))", re.I)
_SETSXREPS = re.compile(r"\b(\d+)\s*[x×*]\s*(\d+)\b", re.I)
_SETS_OF = re.compile(r"\b(\d+)\s*sets?\s*(?:of)?\s*(\d+)(?:\s*reps?)?\b", re.I)
_REPS = re.compile(r"\b(\d+)\s*reps?\b", re.I)
_X_REPS_X_SETS = re.compile(r"[x×]\s*(\d+)\s*[x×]\s*(\d+)\b", re.I)  # "60kg x 10 x 3" = 10 reps, 3 sets
_X_SETS = re.compile(r"[x×]\s*(\d+)\b(?!\s*[x×])", re.I)
_PAIR = re.compile(rf"{_NUM}\s*(?:{_UNIT})?\s*[x×*]\s*(\d+)\b", re.I)
_FILLER = {"at", "of", "set", "sets", "rep", "reps", "did", "i", "for", "with", "and", "then", "x", "kg", "kgs", "lb", "lbs", "a", "the", "my", "today", "did", "do", "done", "@"}


def _to_kg(value: str, unit: str | None) -> float:
    v = float(value.replace(",", "."))
    if unit and unit.lower().startswith(("lb", "pound")):
        v *= LB_TO_KG
    return round(v, 1)


def _parse_segment(seg: str) -> ParsedItem | None:
    s = seg.strip()
    if not s:
        return None
    work = s
    sets: list[ParsedSet] = []
    weight: float | None = None

    m = _W_UNIT.search(work)
    if m:
        weight = _to_kg(m.group(1), m.group(2))
        work = work[: m.start()] + " " + work[m.end():]
    else:
        m = _W_AT.search(work)
        if m:
            weight = _to_kg(m.group(1), None)
            work = work[: m.start()] + " " + work[m.end():]

    if weight is not None:
        n = reps = None
        m = _X_REPS_X_SETS.search(work)
        if m:
            reps, n = int(m.group(1)), int(m.group(2))
            work = work[: m.start()] + " " + work[m.end():]
        for rx in (() if n else (_SETS_OF, _SETSXREPS)):
            m = rx.search(work)
            if m:
                n, reps = int(m.group(1)), int(m.group(2))
                work = work[: m.start()] + " " + work[m.end():]
                break
        if n is None:
            m = _REPS.search(work)
            if m:
                reps = int(m.group(1))
                work = work[: m.start()] + " " + work[m.end():]
                m2 = _X_SETS.search(work) or re.search(r"\b(\d+)\s*sets?\b", work, re.I)
                n = int(m2.group(1)) if m2 else 1
                if m2:
                    work = work[: m2.start()] + " " + work[m2.end():]
        if n and reps:
            sets = [ParsedSet(weight, reps) for _ in range(n)]
    else:
        pairs = list(_PAIR.finditer(work))
        # "80x8, 85x8" or a single "80x5": numbers that look like loads are weight x reps
        if pairs and (len(pairs) >= 2 or float(pairs[0].group(1).replace(",", ".")) >= 20):
            for p in pairs:
                sets.append(ParsedSet(_to_kg(p.group(1), p.group(2)), int(p.group(3))))
            for p in reversed(pairs):
                work = work[: p.start()] + " " + work[p.end():]
        else:
            for rx in (_SETS_OF, _SETSXREPS):
                m = rx.search(work)
                if m:  # bodyweight: sets x reps
                    sets = [ParsedSet(0.0, int(m.group(2))) for _ in range(int(m.group(1)))]
                    work = work[: m.start()] + " " + work[m.end():]
                    break

    name = " ".join(w for w in _norm(work).split() if w not in _FILLER and not w.isdigit())
    if not name or not sets:
        return None
    if len(sets) > 30 or any(x.reps > 500 or x.weight_kg > 1000 for x in sets):
        return None
    return ParsedItem(text=name, sets=sets)


def rule_parse(text: str) -> list[ParsedItem]:
    parts = re.split(r"[\n;]|\bthen\b|\band\b(?=\s+[a-zA-Z])|\.\s+|,\s*(?=[a-zA-Z])", text, flags=re.I)
    items = [i for i in (_parse_segment(p) for p in parts) if i]
    return items


# ------------------------------------------------------------ LLM parser
_SYSTEM = (
    "You extract gym workout logs from a user's message. Reply with ONLY a JSON array, no prose. "
    'Each element: {"exercise": string, "sets": [{"weight_kg": number, "reps": integer}]}. '
    "Convert pounds to kilograms. Use weight_kg 0 for bodyweight. Expand '3x10 at 80kg' into three set objects. "
    "Prefer exercise names from the provided library. Ignore anything that is not a logged exercise. "
    "If nothing is loggable reply []."
)


def _extract_json_array(text: str) -> list:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise LLMError("No JSON array in response")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError("Invalid JSON from model") from e
    if not isinstance(data, list):
        raise LLMError("Expected a JSON array")
    return data


def llm_parse(llm: LLM, text: str, library_names: list[str]) -> list[ParsedItem]:
    prompt = f"Exercise library: {', '.join(library_names)}\n\nUser message:\n{text}"
    raw = llm.complete(_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=800)
    items: list[ParsedItem] = []
    for row in _extract_json_array(raw):
        if not isinstance(row, dict) or not isinstance(row.get("exercise"), str):
            continue
        sets = []
        for s in row.get("sets") or []:
            try:
                w, r = float(s.get("weight_kg", 0)), int(s.get("reps", 0))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= w <= 1000 and 1 <= r <= 500:
                sets.append(ParsedSet(round(w, 1), r))
        if sets and len(sets) <= 30:
            items.append(ParsedItem(text=row["exercise"].strip()[:120], sets=sets))
    return items
