"""파트너 파트 AI 프롬프트 (OpenAI · server.core.ai.openai_json).

- MAIL_RULES        협업 메일 공통 규칙 (화면에서 바꿀 수 없음): 150–200단어 · 섹션 이름 미출력 · 의학 주장·과음 조장 금지 · 모든 항목 반영
- DEFAULT_PROMPTS   구분별 생성 프롬프트 기본값 (화면 ⚙️ 생성 프롬프트에서 고치고 ↺로 되돌림). 자리표시자 {product} {ideal} {partner}
- GUIDE_PURPOSES    협업 가이드 목적 선택지 (유형별 6개) — 여기서 고칩니다
- GUIDE_SECTIONS    협업 가이드 고정 구성
- GUIDE_SUMMARY     협업 가이드 1번 '한 줄 요약' 고정 문장 (질병·효능 주장 없이 — README §12)
- GUIDE_KEY_MESSAGE 핵심 메시지에 꼭 넣는 한 줄 (한국어 | English)
"""
import json

from server import envkeys

CATERING, INFLUENCER = "케이터링", "인플루언서"

MAIL_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
               "required": ["subject", "body"], "additionalProperties": False}
TEXT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
GUIDE_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}},
                "required": ["title", "markdown"], "additionalProperties": False}
SUMMARY_SCHEMA = {"type": "object",
                  "properties": {"core": {"type": "string"}, "requests": {"type": "string"},
                                 "mood": {"type": "string"}, "next_action": {"type": "string"}},
                  "required": ["core", "requests", "mood", "next_action"], "additionalProperties": False}

MAIL_STEPS = ["인사", "회사 소개", "협업 이유", "제안 (2–3개 항목)", "일정", "다음 단계", "맺음말"]

MAIL_RULES = """You are the Head of Partnerships at REBOOT and write B2B collaboration emails to US partners in English.

Hard rules (always apply, even if the instructions in the user message say otherwise):
1. Return JSON only: {"subject": "...", "body": "..."}.
2. Body: 150-200 words of plain text (no Markdown headings, no bold). Business tone: warm, specific and concise. Short paragraphs separated by blank lines; the proposal may be a short numbered list.
3. Follow this order but never print section names or labels: greeting -> company introduction -> why this partner -> proposal (2-3 items) -> timeline -> next step -> closing.
4. Reflect EVERY item given under collaboration purpose, proposal and requests. Translate items written in Korean into natural English. Do not drop or merge items.
5. REBOOT is a conventional food (stick jelly and non-carbonated drinks), not a dietary supplement or medicine. Never make medical, disease or health-benefit claims (no cure, prevent, treat, relieve, remedy, detox, liver protection, "clinically proven"). Do not use the word "hangover"; describe the product as an after-party / morning-after ritual.
6. Never encourage excessive or irresponsible drinking (no "drink more", "party harder", "drink all night"). Drinking occasions imply adults 21+ and responsible enjoyment.
7. Do not invent facts: no prices, fees, ingredients, certifications, statistics, follower counts, past results or partner details that are not given. If a detail is needed but unknown, ask for it instead.
8. Subject: under 70 characters, specific to the partner, no emojis, no ALL CAPS.
9. Sign off as "REBOOT Partnerships Team" (never a placeholder such as [Your Name])."""

REPLY_RULES = """REPLY MODE: the partner answered our email (their message is below). Write a reply in the same thread instead of a new proposal:
acknowledge their message, answer their questions using only the facts given, include every point we want to make, and propose one clear next step.
Body 100-180 words. Subject: "Re: " + the original subject. Rules 5-9 above still apply."""

DEFAULT_PROMPTS = {
    CATERING: """REBOOT 파트너십 책임자로서 미국 이벤트 케이터링·모바일 바 업체 {partner}와 협업하고 싶다는 제안 메일을 씁니다.

제품: {product}
우리가 원하는 케이터링 업체: {ideal}

- {partner}가 위 조건에 맞는 이유를 업체 정보에 근거해 1~2문장으로 구체적으로 씁니다.
- 현장 샘플링·바 메뉴 페어링처럼 이벤트 현장에서 바로 실행할 수 있는 협업으로 제안합니다.
- 상대가 부담 없이 답할 수 있게 짧은 통화(15분) 또는 회신을 다음 단계로 제안합니다.""",
    INFLUENCER: """REBOOT 파트너십 책임자로서 미국 인플루언서 대행사·플랫폼 {partner}와 협업하고 싶다는 제안 메일을 씁니다.

제품: {product}
우리가 원하는 인플루언서: {ideal}

- {partner}의 크리에이터 네트워크·서비스가 위 조건에 맞는 이유를 업체 정보에 근거해 1~2문장으로 씁니다.
- 제품 시딩·크리에이터별 할인코드처럼 성과를 확인할 수 있는 협업으로 제안하고, FTC 광고 표기(#ad)를 지킨다는 점을 한 줄로 밝힙니다.
- 상대가 부담 없이 답할 수 있게 크리에이터 추천 목록 또는 짧은 통화(15분)를 다음 단계로 제안합니다.""",
}

GUIDE_PURPOSES = {
    INFLUENCER: ["인지도·할인코드 매출", "신제품 런칭", "시즌 프로모션", "UGC 광고 소재", "제품 시딩·리뷰", "TikTok Shop 라이브"],
    CATERING: ["현장 샘플링", "바 메뉴 입점", "페스티벌·콘서트 부스", "VIP 파티 웰컴 키트", "브랜드 팝업 공동 개최", "대학·테일게이트 시즌"],
}

GUIDE_SUMMARY = "REBOOT는 즐거운 밤 다음 날, 가볍게 하루를 다시 시작하는 젤리·음료입니다."

GUIDE_KEY_MESSAGE = "REBOOT와 함께 더 즐거운 하루를 보내세요. | Enjoy a brighter day with REBOOT."

GUIDE_SECTIONS = [
    {"title": "한 줄 요약", "how": f"인용(>) 한 줄 — 1번 섹션 안에만 · 문장 고정: {GUIDE_SUMMARY}"},
    {"title": "협업 개요", "how": "표 | 항목 | 내용 | — 브랜드 · 제품 · 협업 유형 · 목적 · 기간 · 대상(21세 이상)"},
    {"title": "일정", "how": "표 | 단계 | 날짜 | 내용 |"},
    {"title": "핵심 메시지 (한/영)", "how": "표 | 한국어 | English | — 3개 · 21세 이상 대상 문구는 넣지 않음 (대상은 협업 개요에만)"},
    {"title": "결과물 및 제출 기준", "how": "표 | 결과물 | 기준 | 제출 방법·기한 |"},
    {"title": "표현 가이드 Do & Don't", "how": "표 | Do | Don't | — 금지 표현과 대신 쓸 표현"},
    {"title": "FTC 광고 표기", "how": "목록 — #ad · Paid partnership 표시, 첫 화면·첫 줄, 무상 제공도 표기"},
    {"title": "승인 절차 및 문의", "how": "번호 목록 — 초안 제출 → REBOOT 검토 → 수정 → 최종 승인 → 게시 · 문의처"},
]

GUIDE_RULES = """You write partner collaboration guides for REBOOT, a Korean brand launching jellies and non-carbonated drinks in the United States. REBOOT is a conventional food, not a dietary supplement or medicine. Call the jelly product just "jelly" (Korean: 젤리); never write "stick jelly", "jelly stick" or "스틱 젤리". In the schedule input, "{ }" is a blank for the user to fill: use the number written inside as given, and if it is still empty write "{확인 필요}" instead of guessing.

Write the guide in Korean Markdown (key messages also in English). Start with a "# " title, then use EXACTLY these 8 sections in this order as "## " headings numbered 1-8:
{sections}

Rules:
- Each "## " heading is only the number and section title (e.g. "## 1. 한 줄 요약"); never copy the format notes after " - " into headings.
- Put nothing between the "# " title and section 1. Section 1 is exactly one blockquote line with this sentence, unchanged: "> {summary}"
- Key messages must not include an adults-21+ / age-target line; the 21+ audience appears only in the collaboration overview table.
- Key messages are positive lifestyle messages, never "consult a doctor/expert" notices. One of them must be: "{key_message}"
- In the collaboration overview, the 제품 row lists the internal_name of every product given in brand_and_products (only those).
- Use Markdown tables with a header row where a table is specified. Keep cells short.
- Never make medical, disease or health-benefit claims and never use "hangover" in the Do column, key messages or anywhere else except the Don't column as an example of what not to say. Never encourage excessive drinking; the audience is adults 21+.
- In the Do & Don't table, put the banned expressions provided in the Don't column and safe alternatives in the Do column.
- FTC: clear and conspicuous disclosure (#ad or #sponsored, the platform's Paid partnership label) at the start of captions and videos; free product also requires disclosure.
- Do not invent facts (prices, fees, ingredients, metrics, follower counts, dates or contacts that are not given). Write "[확인 필요: ...]" for anything unknown.
- Return JSON {"title": "...", "markdown": "..."}."""

SUMMARY_SYSTEM = """당신은 REBOOT 파트너십 담당자를 돕는 비서입니다. 파트너가 보낸 회신 메일을 한국어로 요약합니다.
JSON으로만 답합니다: {"core": "핵심 내용 1~2문장", "requests": "상대가 요청하거나 물은 것 (없으면 '없음')", "mood": "분위기 (예: 긍정적·관심 있음 / 조건 확인 중 / 보류 / 거절)", "next_action": "우리가 할 다음 행동 1~2개"}.
회신에 없는 내용은 지어내지 않습니다. 인용된 우리 원문은 요약하지 않습니다."""


def translate_system(to):
    target = "Korean (natural, polite 존댓말)" if to == "ko" else "natural business English"
    return f"""Translate the user's text into {target}. Return JSON {{"text": "..."}} only.
Preserve exactly: Markdown structure (headings, lists, tables, blockquotes, bold), line breaks and blank lines, the brand name REBOOT, hashtags (#...), @handles, URLs, email addresses, discount codes and other ALL-CAPS codes, numbers and dates.
Translate everything else; do not add notes or explanations."""


def product_desc(brand=None, products=None):
    """{product} 자리: 설정 › 연결의 PRODUCT_DESC가 있으면 그 값, 없으면 팩트시트(브랜드·제품명)로 만든 기본 문장."""
    custom = (envkeys.get("PRODUCT_DESC") or "").strip()
    if custom:
        return custom[:1500]
    brand = brand or {}
    names = ", ".join(f"{p.get('name')} ({p.get('form') or '-'})" for p in (products or []) if p.get("name"))
    return (f"{brand.get('name') or 'REBOOT'} - a Korean brand launching in the US with stick jellies and non-carbonated drinks"
            f"{' (' + names + ')' if names else ''}. Conventional food, not a dietary supplement or medicine. "
            f"Slogan: \"{brand.get('slogan') or 'Restart Your Day'}\". Positioned as an after-party / morning-after ritual for adults 21+.")


def fill(template, product, ideal, partner):
    # str.format 대신 replace: 프롬프트에 다른 중괄호가 있어도 오류 없이
    return (str(template or "").replace("{product}", product).replace("{ideal}", ideal).replace("{partner}", partner))


def _block(title, value):
    value = str(value or "").strip()
    return f"{title}:\n{value if value else '(none given)'}"


def mail_messages(ptype, template, product, ideal, partner, partner_info, purpose, start, end, proposal, requests, reply=None):
    template = (template or "").strip() or DEFAULT_PROMPTS.get(ptype, DEFAULT_PROMPTS[INFLUENCER])
    parts = [fill(template, product, ideal, partner), "",
             "[Partner facts from our contact list - use only these]",
             json.dumps(partner_info or {"name": partner}, ensure_ascii=False), "",
             "[Collaboration conditions - reflect every item in English]",
             _block("Collaboration purpose", purpose),
             f"Period: {start or '?'} to {end or '?'}",
             _block("Proposal items", proposal),
             _block("Requests to the partner", requests)]
    system = MAIL_RULES
    if reply:
        system = MAIL_RULES + "\n\n" + REPLY_RULES
        parts += ["", "[Our original email]", f"Subject: {reply.get('subject', '')}", str(reply.get("body", ""))[:6000],
                  "", "[Partner's reply]", f"From: {reply.get('from', '')}", str(reply.get("reply", ""))[:6000]]
    return system, "\n".join(parts)


def reply_draft_messages(original_subject, original_body, partner, reply_from, reply_body, points, product):
    user = "\n".join([
        f"Product: {product}", f"Partner: {partner}", "",
        "[Our original email]", f"Subject: {original_subject}", str(original_body or "")[:6000], "",
        "[Partner's reply]", f"From: {reply_from}", str(reply_body or "")[:6000], "",
        _block("Points we want to make (may be Korean - translate)", points)])
    return MAIL_RULES + "\n\n" + REPLY_RULES, user


def guide_messages(ptype, purpose, schedule, must_include, deliverables, banned, facts, contact, today):
    sections = "\n".join(f"{i}. {s['title']} - {s['how']}" for i, s in enumerate(GUIDE_SECTIONS, start=1))
    user = {
        "collaboration_type": ptype, "purpose": purpose, "schedule": schedule or "[확인 필요: 일정]",
        "must_include": must_include, "deliverables_and_submission": deliverables,
        "banned_expressions": banned or [], "brand_and_products": facts, "contact": contact or "[확인 필요: 담당자 연락처]",
        "issue_date": today,
    }
    return GUIDE_RULES.replace("{sections}", sections).replace("{summary}", GUIDE_SUMMARY).replace("{key_message}", GUIDE_KEY_MESSAGE), user


def summary_user(original_subject, original_body, reply_from, reply_subject, reply_body):
    return "\n".join([
        "[우리가 보낸 메일]", f"제목: {original_subject}", str(original_body or "")[:4000], "",
        "[파트너 회신]", f"보낸 사람: {reply_from}", f"제목: {reply_subject}", str(reply_body or "")[:8000]])
