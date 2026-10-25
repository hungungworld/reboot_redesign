"""정확도 검증 — 유튜브 댓글 VOC 분류를 사람 정답(data/comment_examples.json의 use=test)으로 채점.

- 규칙 / AI(예시 없이) / AI(예시 포함, 퓨샷) 세 방식을 같은 댓글로 분류해 맞은 비율을 비교
- 채점용 댓글은 퓨샷 예시와 겹치지 않음
- 결과는 insights_kv 'accuracy'에 저장 (데이터 › 정확도 검증 · README '정확도 검증')
"""
import math

from server.core.ai import openai_model
from server.models import iso, utcnow
from . import ai as insights_ai
from . import classify
from .models import kv_get, kv_set

BATCH = 40
METHODS = [("rule", "규칙 (단어)"), ("zero", "AI · 예시 없이"), ("few", "AI · 예시 포함 (퓨샷)")]
FIELDS = [("relevant", "관련 여부"), ("question", "질문 여부"), ("topic", "질문 주제"), ("exact", "세 칸 모두")]


def split():
    items = insights_ai.comment_examples()
    return [x for x in items if x["use"] == "example"], [x for x in items if x["use"] == "test"]


def _norm(relevant, question, topic):
    """update._run_voc와 같은 규칙: 관련 없으면 질문 아님, 주제를 모르면 질문 아님."""
    relevant = bool(relevant)
    question = bool(question) and relevant and topic not in ("", "none", None)
    return {"relevant": relevant, "question": question, "topic": topic if question else "none"}


def score(gold, pred):
    """gold · pred: [{relevant, question, topic}] 같은 순서 → {field: 맞은 비율(%)} + 틀린 댓글 (번호 · 분류 결과)."""
    n = len(gold)
    hit = {f: 0 for f, _ in FIELDS}
    wrong = []
    for g, p in zip(gold, pred):
        ok = {f: g[f] == p[f] for f in ("relevant", "question", "topic")}
        ok["exact"] = all(ok.values())
        for f, v in ok.items():
            hit[f] += v
        if not ok["exact"]:
            wrong.append({"no": g["no"], "pred": p})
    return {"n": n, **{f: round(hit[f] * 100 / n, 1) if n else None for f, _ in FIELDS}, "wrong": wrong}


def _ai_preds(tests, few_shot):
    out = {}
    for i in range(0, len(tests), BATCH):
        batch = tests[i:i + BATCH]
        out.update(insights_ai.classify_comments([{"id": str(x["no"]), "text": x["text"][:500]} for x in batch], few_shot=few_shot))
    preds = []
    for x in tests:
        o = out.get(str(x["no"])) or {}
        preds.append(_norm(o.get("relevant"), o.get("question"), o.get("topic")))
    return preds


def calls_needed(ai):
    return 2 * math.ceil(len(split()[1]) / BATCH) if ai else 0


def run(user=""):
    examples, tests = split()
    gold = [dict(_norm(x["relevant"], x["question"], x["topic"]), no=x["no"]) for x in tests]
    methods = {}
    rule = [classify.classify_comment(x["text"]) for x in tests]
    methods["rule"] = score(gold, [_norm(r["relevant"], r["is_question"], r["topic"]) for r in rule])
    ai = insights_ai.ready()
    if ai:
        methods["zero"] = score(gold, _ai_preds(tests, few_shot=False))
        methods["few"] = score(gold, _ai_preds(tests, few_shot=True))
    winner = max(methods, key=lambda k: (methods[k]["exact"], k == "few"))
    result = {"at": iso(utcnow()), "by": user, "model": openai_model() if ai else "", "examples": len(examples),
              "tests": len(tests), "methods": methods, "winner": winner}
    kv_set("accuracy", result, user)
    return result, calls_needed(ai)


def _counts(xs):
    return {"total": len(xs), "relevant": sum(x["relevant"] for x in xs), "question": sum(x["question"] for x in xs),
            "topics": {t: sum(1 for x in xs if x["question"] and x["topic"] == t) for t in classify.QUESTION_LABEL}}


def view():
    examples, tests = split()
    by_no = {x["no"]: x for x in tests}
    saved = kv_get("accuracy", {}) or None
    if saved:  # 틀린 댓글은 화면에서 바로 볼 수 있게 문장 · 정답을 붙임
        for m in saved["methods"].values():
            for w in m["wrong"]:
                g = by_no.get(w["no"])
                if g:
                    w.update(text=g["text"], gold=_norm(g["relevant"], g["question"], g["topic"]))
    return {"saved": saved, "aiReady": insights_ai.ready(), "methods": METHODS, "fields": FIELDS,
            "topicLabels": classify.QUESTION_LABEL, "exampleCounts": _counts(examples), "testCounts": _counts(tests),
            "examples": [{"no": x["no"], "text": x["text"], "reason": x["reason"],
                          **_norm(x["relevant"], x["question"], x["topic"])} for x in examples]}
