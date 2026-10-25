"""프롬프트 개선 전후 비교 (프롬프트 로그용) — `python -m tabs.compliance.tools.prompt_experiment`

같은 문장을 AI 2차 판정 v2(few-shot 없음)와 v3(few-shot 5개)로 판정해 docs/prompt-experiments/<날짜>.json에 남깁니다.
문장은 data.js '처음 보는 문장 시험' 중 규칙이 틀린 문장 + 괜찮은 문장 (few-shot 예시와 겹치지 않음).
실제 Claude를 부르므로 비용이 듭니다 (문장 8개 × 2버전 = 16회 · 사용량은 ai_usage에 기록).
"""
import json
from datetime import date
from pathlib import Path

from tabs.compliance import ai_review, server_review

CASES = [   # (문장, 팀이 정한 정답) — data.js evalSet과 같은 값
    ("Wake up without the headache.", "BLOCK"),
    ("Helps your body process alcohol faster.", "BLOCK"),
    ("Party all night and feel fine tomorrow.", "BLOCK"),
    ("Doctors recommend our jelly.", "REVISE"),
    ("Absorbs alcohol so you stay sharp.", "BLOCK"),
    ("Try it risk-free with our 30-day money-back guarantee.", "PASS"),
    ("Tastes like lime and cherry.", "PASS"),
    ("Made in Korea with Hovenia dulcis extract.", "PASS"),
]
OUT = Path(__file__).parent.parent / "docs" / "prompt-experiments"


def _rules():
    data = json.loads(server_review.RULES_FILE.read_text(encoding="utf-8"))
    return [{"code": r["code"], "name": r["name"], "verdict": r["verdict"], "reason": ""} for r in data["rules"]]


def run():
    rules, rows = _rules(), []
    for version, system in (("v2 (few-shot 없음)", ai_review.SYSTEM_BASE), ("v3 (few-shot 5개)", ai_review.SYSTEM)):
        ai_review.SYSTEM = system
        for text, expect in CASES:
            rule = server_review.review(text, "b2c")
            try:
                out = ai_review.review(text, channel="B2C 광고·SNS·아마존", rule={"verdict": rule["verdict"], "codes": rule["codes"]}, rules=rules)
                ai = out.get("verdict", "")
                note = out.get("summary", "")
            except ai_review.ReviewError as e:
                ai, note = "실패", str(e)
            rows.append({"version": version, "text": text, "expect": expect, "rule": rule["verdict"], "ai": ai, "summary": note})
            print(f"{version} | 정답 {expect:6} | 규칙 {rule['verdict']:6} | AI {ai:6} | {text}")
    ai_review.SYSTEM = ai_review.SYSTEM_BASE + "\n\n" + ai_review.FEW_SHOT
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{date.today().isoformat()}.json"
    path.write_text(json.dumps({"date": date.today().isoformat(), "model": ai_review.MODEL, "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("저장:", path)
    return rows


if __name__ == "__main__":
    from server import create_app
    with create_app().app_context():
        run()
