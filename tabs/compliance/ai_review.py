"""컴플라이언스 AI 2차 판정 (Claude).

규칙 판정(R1~R14)을 대신하지 않습니다. 당 함량 표현은 팀 결정(2026-10-24, R11·R12 끔)에 따라 AI도 판정하지 않습니다. 규칙 결과를 함께 넘겨 '규칙이 놓친 위험'과
'더 안전한 문장'을 참고용으로 받습니다. 최종 사용 여부는 담당자 승인으로 정합니다.

Flask 통합: 키는 server.envkeys(설정 › 연결 화면 → .env)에서 읽습니다. 로직은 원본(JUNHEE/server)과 같습니다.
"""
import json
import sys

import anthropic

from server.envkeys import get as env

from . import ai_usage

MODEL = "claude-opus-5-5"
VERDICTS = ["BLOCK", "REVISE", "COND", "REVIEW", "PASS"]
MAX_TEXT = 2000

SYSTEM = """You review English advertising copy for REBOOT (slogan "Restart Your Day"), a Korean after-party brand sold in the United States.
The line-up is four conventional foods, not dietary supplements and not drugs: Reboot Jelly Original (stick jelly, individually wrapped sticks),
Reboot Jelly Balance (a sugar-reduced stick jelly), Reboot Drink Original (a non-carbonated drink in small bottles, 6 per box) and Reboot Drink Balance (a sugar-reduced version of that drink).
All four come in lime, cherry and lemon (flavors are tentative).
The request names which product the copy is for.

Assess the copy the way a careful US food-advertising compliance reviewer would:
- FDA: a conventional food must not claim to prevent, treat, cure or mitigate a disease or condition (a hangover claim reads as a drug claim); structure/function claims on a conventional food must come from the food's nutritive value; no implied medical or clinical authority.
- FTC: every objective claim needs competent and reliable substantiation; testimonials, reviews and endorsements must be genuine and disclosed; no incentivised reviews; no claims that encourage excessive drinking.
- Sugar-content wording (low sugar, zero sugar, less/reduced sugar, sugar free, no added sugar) is outside this review by team decision (rules R11-R12 are switched off): do not list it as an issue and do not let it change the verdict. Still judge any health or hangover claim in the same sentence as usual.
- The team's own rules R1-R14 are listed below. Use their codes when an issue matches one; use "OTHER" for a risk the rules do not cover.

Verdict scale (pick the strictest that applies):
BLOCK = must not be used as written; REVISE = needs rewording; COND = acceptable only under a stated condition (channel, substantiation, disclosure); REVIEW = a human must judge; PASS = no issue found.
When you are unsure, choose REVIEW rather than PASS. PASS never means legal approval.

Write `summary` and each `reason` in Korean (plain, short sentences for a marketer). Write `phrase`, `suggestion` and `rewrite` in English.
`rewrite` is one safer version of the whole copy that keeps its intent; leave it empty when the verdict is PASS.
Do not invent product facts (ingredients, test results, certifications) in suggestions or the rewrite.
In `checks` list 2-4 short Korean notes on what you checked before deciding (claim type, what a US reader would infer, channel or disclosure needs) and in `uncertain` say in Korean what you could not verify (empty string if nothing)."""

# few-shot 예시 (2026-10-24 · 프롬프트 v3). 규칙이 놓치는 '돌려 말하기'와 괜찮은 사실 문장을 함께 보여 줌.
# '처음 보는 문장 시험'(data.js evalSet) 문장은 넣지 않음 → 시험 점수가 예시를 외운 결과가 되지 않게.
FEW_SHOT = """Worked examples (abbreviated output; follow the same reasoning, not the same wording):

1. Copy: "Our blend helps stop hangovers before they start."
   -> BLOCK. issues: [{phrase: "stop hangovers", code: "R2", verdict: "BLOCK", reason: "숙취를 막는다는 말은 질병 예방 주장이라 일반식품에 쓸 수 없음"}]
2. Copy: "Never miss a morning meeting after the office party again."
   -> BLOCK. No banned word, but a US reader infers the product prevents hangover symptoms after drinking (implied drug claim).
   issues: [{phrase: "Never miss a morning meeting after the office party", code: "OTHER", verdict: "BLOCK", reason: "숙취 단어 없이 숙취를 막아 준다고 암시함"}]
3. Copy: "Trusted by pharmacists across Korea."
   -> REVISE. Medical-professional authority makes a food look like a medicine and needs substantiation.
   issues: [{phrase: "Trusted by pharmacists", code: "R7", verdict: "REVISE", reason: "약사 권위를 빌려 의약품처럼 보이게 함 · 근거 필요"}]
4. Copy: "Post an honest review and we'll send a thank-you gift."
   -> REVISE. Not conditioned on a positive review, so not an outright FTC violation, but the incentive must be disclosed and many platforms (e.g. Amazon) prohibit it.
   issues: [{phrase: "send a thank-you gift", code: "R10", verdict: "REVISE", reason: "후기 대가는 표시가 필요하고 플랫폼 정책상 금지될 수 있음"}]
5. Copy: "Each box has 10 individually wrapped lime jelly sticks."
   -> PASS. A verifiable product fact with no health, drinking or performance claim. issues: []"""

SYSTEM_BASE = SYSTEM   # few-shot 없는 이전 버전 (prompt_experiment.py가 비교용으로 씀)
SYSTEM = SYSTEM_BASE + "\n\n" + FEW_SHOT

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string"},
                    "code": {"type": "string"},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "reason": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["phrase", "code", "verdict", "reason", "suggestion"],
                "additionalProperties": False,
            },
        },
        "rewrite": {"type": "string"},
        "checks": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "string"},
    },
    "required": ["verdict", "confidence", "summary", "checks", "uncertain", "issues", "rewrite"],
    "additionalProperties": False,
}


class ReviewError(Exception):
    """화면에 그대로 보여 줄 수 있는 실패 (키·내부 정보 없음)."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def available():
    return env("ANTHROPIC_API_KEY") != ""


def _rules_text(rules):
    lines = []
    for r in (rules or [])[:30]:
        if not isinstance(r, dict):
            continue
        lines.append(f'{str(r.get("code", ""))[:8]} {str(r.get("name", ""))[:80]} — default {str(r.get("verdict", ""))[:8]}: {str(r.get("reason", ""))[:300]}')
    return "Team rules:\n" + ("\n".join(lines) if lines else "(not provided)")


MAX_PERSONA = 600
PERSONA_INTRO = ("Reviewer persona chosen by the team. It sets your perspective and tone only: the FDA/FTC standards, "
                 "the team rules, the verdict scale and the output format above always take precedence, and the persona "
                 "never makes a verdict less strict.\n")


def review(text, channel="", category="FOOD", rule=None, rules=None, product="", persona=""):
    text = str(text or "").strip()
    if not text:
        raise ReviewError("점검할 문구가 없습니다.", 400)
    if len(text) > MAX_TEXT:
        raise ReviewError(f"문구가 너무 깁니다 ({MAX_TEXT}자 이하).", 400)
    if not available():
        raise ReviewError("ANTHROPIC_API_KEY가 없습니다 (설정 › 연결 또는 .env).", 503)

    budget = ai_usage.over_budget()
    if budget:
        raise ReviewError(budget, 429)

    rule = rule if isinstance(rule, dict) else {}
    payload = {
        "copy": text,
        "channel": str(channel)[:60],
        "standard": "dietary supplement (comparison only)" if category == "SUPPLEMENT" else "conventional food",
        "product": str(product)[:300],
        "rule_engine": {
            "verdict": str(rule.get("verdict", ""))[:8],
            "codes": [str(c)[:8] for c in (rule.get("codes") or [])][:10],
            "matched_terms": [str(t)[:60] for t in (rule.get("terms") or [])][:20],
        },
    }
    # 페르소나(화면 '단건 심의 → AI 검토자 페르소나')는 관점·말투만. 기준·판정 단계는 SYSTEM이 우선
    persona = str(persona or "").strip()[:MAX_PERSONA]
    system = [{"type": "text", "text": SYSTEM}]
    if persona:
        system.append({"type": "text", "text": PERSONA_INTRO + persona})
    # 규칙 목록·페르소나는 매번 거의 같으니 시스템 프롬프트 끝에 캐시 지점
    system.append({"type": "text", "text": _rules_text(rules), "cache_control": {"type": "ephemeral"}})
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), timeout=120.0)
    try:
        res = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": "Review this ad copy. Input as JSON:\n" + json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError:
        raise ReviewError("ANTHROPIC_API_KEY가 올바르지 않습니다 (설정 › 연결 확인).", 503)
    except anthropic.PermissionDeniedError:
        raise ReviewError("이 API 키로는 해당 모델을 쓸 수 없습니다.", 503)
    except anthropic.RateLimitError:
        raise ReviewError("요청이 많아 잠시 막혔습니다. 1분 뒤 다시 시도하세요.", 429)
    except anthropic.APIStatusError as e:
        raise ReviewError(f"AI 서버 오류 ({e.status_code}). 잠시 뒤 다시 시도하세요.")
    except anthropic.APIConnectionError:
        raise ReviewError("AI 서버에 연결하지 못했습니다 (인터넷 확인).")
    ai_usage.record("compliance", "AI 2차 판정", res)

    if res.stop_reason == "refusal":
        raise ReviewError("AI가 이 문구의 판정을 거절했습니다. 규칙 판정과 담당자 검토로 진행하세요.", 422)
    if res.stop_reason == "max_tokens":
        raise ReviewError("AI 응답이 길어 잘렸습니다. 문구를 줄여 다시 시도하세요.")
    body = next((b.text for b in res.content if b.type == "text"), "")
    try:
        out = json.loads(body)
    except ValueError:
        raise ReviewError("AI 응답을 읽지 못했습니다. 다시 시도하세요.")
    out["model"] = res.model
    return out


if __name__ == "__main__":
    # 한 문장 시험: python -m tabs.compliance.ai_review "Prevents hangovers."
    from server import create_app
    with create_app().app_context():
        sample = " ".join(sys.argv[1:]) or "Prevents hangovers."
        try:
            print(json.dumps(review(sample, channel="B2C 광고·SNS"), ensure_ascii=False, indent=2))
        except ReviewError as e:
            sys.exit(f"실패: {e}")
