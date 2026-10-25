"""콘텐츠 제작 기준 목록 — 화면 tabs/content/static/data.js와 같은 ID를 씁니다 (tabs/content/tests/test_content.py가 확인).

제품 목록은 여기 두지 않고 공용 팩트시트(server/catalog)에서 읽습니다.
"""

# 만들 콘텐츠 (README 2.2) → 기본 텍스트 결과물 종류
CONTENT_TYPES = {
    "image": {"label": "이미지", "kind": "copy"},
    "cardnews": {"label": "카드뉴스", "kind": "cardnews"},
    "shortform": {"label": "숏폼 영상", "kind": "script"},
    "crm": {"label": "CRM 메시지", "kind": "crm"},
}
# 없앤 콘텐츠 종류 — 예전에 만든 캠페인은 DB에 남기고 목록·열기에서 숨김
RETIRED_TYPES = ("product_desc", "plan")

# 게시 채널 (하나만 선택) — short는 자동 캠페인명용. yamibuy는 다른 탭도 쓰는 ID라 그대로 두고 표시 이름만 Yami
CHANNELS = {
    "instagram": {"label": "Instagram", "short": "인스타"},
    "tiktok": {"label": "TikTok", "short": "틱톡"},
    "amazon": {"label": "Amazon", "short": "아마존"},
    "weee": {"label": "Weee", "short": "Weee"},
    "yamibuy": {"label": "Yami", "short": "Yami"},
    "oliveyoung": {"label": "Global Olive Young", "short": "올리브영"},
    "crm": {"label": "CRM", "short": "CRM"},
}
# CRM 콘텐츠는 CRM 채널하고만 (나머지 조합은 자유)
CRM_TYPE, CRM_CHANNEL = "crm", "crm"

STATUSES = ("제작 중", "검토 중", "완료")

# 결과물(버전) 종류 — desc·plan은 숨긴 옛 캠페인 표시용 이름만 남김
KIND_LABELS = {
    "copy": "카피", "cardnews": "카드뉴스", "script": "숏폼 구성안", "desc": "상품 설명",
    "crm": "CRM 메시지", "plan": "기획서", "image": "이미지", "video": "영상",
}
TEXT_KINDS = ("copy", "cardnews", "script", "crm")

# 모델의 action_request.type → 텍스트 결과물 종류
ACTION_KINDS = {
    "draft_copy": "copy", "draft_cardnews": "cardnews", "draft_script": "script", "draft_crm": "crm",
}
ACTION_TYPES = ("none",) + tuple(ACTION_KINDS) + ("generate_image", "edit_image", "generate_video", "select_version")
INTENTS = ("question", "idea", "update_decision", "draft", "generate_media", "edit", "compare_versions",
           "select_version", "save", "chitchat", "other")

SHEET_LABELS = {
    "content_type": "콘텐츠", "channels": "게시 채널", "brand_focus": "브랜드 자체",
    "product_ids": "제작 대상 제품", "goal": "목적", "extra_request": "추가 요청",
}

# 창작 결정 항목 (decisions.<키>) — 목록에 없는 영문 키도 허용, 표시 이름만 여기서
DECISION_LABELS = {
    "concept": "콘셉트", "background": "배경", "tone": "말투·톤", "style": "스타일", "layout": "제품 배치",
    "ratio": "비율", "language": "결과물 언어", "cta": "CTA", "headline": "헤드라인", "message": "핵심 메시지",
    "target": "타깃", "length": "길이", "format": "형식", "season": "시즌", "accent_color": "포인트 색",
    "featured_product": "메인 제품", "slide_count": "장 수", "duration": "영상 길이", "lighting": "조명",
    "composition": "구도", "mood": "분위기", "caption": "캡션", "hashtags": "해시태그", "subtitle": "자막",
    "use_character": "캐릭터 Rebu", "flavor": "맛",
    "story": "스토리", "design_note": "시안 설명", "emphasis": "강조할 내용", "content_points": "담을 내용", "hook": "멘트",
    "slogan_choice": "쓸 슬로건",
}

# 콘텐츠별 대화 흐름 가이드 (순서를 강제하지 않는 체크리스트 · 화면 '브리프' 표 순서) — data.js flows와 같은 키
# 비율 · 크기(ratio)는 챗봇이 채널에 맞는 추천을 보기 첫 번째로 붙여 묻는다 (2026-10-25)
CONTENT_FLOWS = {
    "image": ["concept", "story", "design_note", "emphasis", "ratio"],
    "cardnews": ["content_points", "concept", "emphasis", "slide_count", "ratio"],
    "shortform": ["concept", "duration", "ratio", "story", "design_note", "emphasis"],
    "crm": ["format", "tone", "cta", "target"],
}
# 이 채널이면 초안을 만든 뒤 캡션 · 멘트(게시글 첫 줄 훅 · 고정 댓글)를 다듬자고 제안 (흐름 질문에는 넣지 않음)
SOCIAL_CHANNELS = ("instagram", "tiktok")

# 이미지 · 카드뉴스 캠페인을 만들 때 채널 기본 비율을 '제안'으로 넣음 — 확정은 챗봇이 물어서
RATIO_TYPES = ("image", "cardnews")
CHANNEL_RATIOS = {
    "instagram": "4:5 세로", "tiktok": "9:16 세로", "amazon": "1:1 정사각", "weee": "1:1 정사각",
    "yamibuy": "1:1 정사각", "oliveyoung": "1:1 정사각",
}
# 채널별 비율 · 크기 보기 (첫 번째가 추천) — (보기 이름, 저장 값, 이유). 플랫폼 규정은 운영 전 최신 확인 대상
CHANNEL_RATIO_OPTIONS = {
    "instagram": [("4:5 세로 (추천)", "4:5 세로 · 1080×1350", "피드에서 화면을 가장 많이 차지해요"),
                  ("1:1 정사각", "1:1 정사각 · 1080×1080", "그리드에서 잘리지 않는 기본 비율이에요"),
                  ("9:16 세로", "9:16 세로 · 1080×1920 (스토리·릴스)", "스토리·릴스 전체 화면용이에요")],
    "tiktok": [("9:16 세로 (추천)", "9:16 세로 · 1080×1920", "TikTok 전체 화면 기본 비율이에요"),
               ("4:5 세로", "4:5 세로 · 1080×1350", "사진 모드 · 다른 채널과 같이 쓰기 좋아요"),
               ("1:1 정사각", "1:1 정사각 · 1080×1080", "위아래 여백이 생기지만 어디든 쓰기 쉬워요")],
    "_market": [("1:1 정사각 (추천)", "1:1 정사각 · 2000×2000", "상품 썸네일 · 상세 이미지 기본 비율이에요"),
                ("4:5 세로", "4:5 세로 · 1600×2000", "상세페이지에서 세로로 더 크게 보여요"),
                ("16:9 가로", "16:9 가로 · 1920×1080 (배너)", "몰 배너 · A+ 콘텐츠 가로 컷용이에요")],
}
SHORTFORM_RATIO_OPTIONS = [("9:16 세로 (추천)", "9:16 세로", "숏폼 전체 화면 기본 비율이에요"),
                           ("1:1 정사각", "1:1 정사각", "피드 · 몰 상세에 같이 쓰기 좋아요"),
                           ("16:9 가로", "16:9 가로", "유튜브 · 배너용 가로 영상이에요")]


def ratio_options(sheet):
    """챗봇이 비율 · 크기를 물을 때 보기 3개 (채널 추천이 첫 번째)."""
    if sheet.get("content_type") == "shortform":
        return SHORTFORM_RATIO_OPTIONS
    ch = next(iter(sheet.get("channels") or []), "")
    return CHANNEL_RATIO_OPTIONS.get(ch) or CHANNEL_RATIO_OPTIONS["_market"]


def flow_of(sheet):
    return list(CONTENT_FLOWS.get(sheet.get("content_type"), []))


def default_ratio(sheet):
    if sheet.get("content_type") not in RATIO_TYPES:
        return None
    return next((CHANNEL_RATIOS[ch] for ch in sheet.get("channels") or [] if ch in CHANNEL_RATIOS), None)
# 모델이 한국어 키를 줄 때
DECISION_ALIASES = {
    "배경": "background", "톤": "tone", "말투": "tone", "콘셉트": "concept", "컨셉": "concept", "언어": "language",
    "비율": "ratio", "스타일": "style", "배치": "layout", "헤드라인": "headline", "타깃": "target", "타겟": "target",
    "길이": "length", "형식": "format", "시즌": "season", "핵심메시지": "message", "핵심 메시지": "message",
    "구도": "composition", "조명": "lighting", "분위기": "mood", "자막": "subtitle",
    "캐릭터": "use_character", "리부": "use_character", "rebu": "use_character", "character": "use_character",
    "mascot": "use_character", "맛": "flavor",
    "스토리": "story", "시안": "design_note", "시안설명": "design_note", "시안 설명": "design_note", "강조": "emphasis",
    "강조점": "emphasis", "담을내용": "content_points", "담을 내용": "content_points", "멘트": "hook", "훅": "hook",
    "크기": "ratio", "사이즈": "ratio", "size": "ratio", "aspect_ratio": "ratio",
    "슬로건선택": "slogan_choice", "쓸 슬로건": "slogan_choice", "슬로건 선택": "slogan_choice",
}

# 제품·브랜드 사실에 해당하는 키 — 대화로 바꾸지 않고 '확인 대기'로 기록 (README 3.4)
FACT_KEYS = frozenset({
    "ingredients", "ingredient", "price", "cost", "volume", "size_ml", "ml", "weight", "net_weight", "calories", "kcal",
    "sugar", "sugar_g", "sugar_content", "sugar_reduction", "nutrition", "nutrition_facts", "certification",
    "certifications", "efficacy", "health_claim", "benefit_claim", "stick_count", "pack_count", "bottle_size",
    "flavors", "flavor_lineup", "product_name", "consumer_name", "allergens", "origin", "slogan", "brand_name",
})

# 접어 둔 고급 옵션 (README 4.2 초기 제안값)
DEFAULT_SETTINGS = {"answer_length": "short", "candidates": 3, "rewrite_max": 1, "video_mode": "plan_only",
                    "image_provider": "openai"}
ANSWER_LENGTHS = {"short": "짧게 (2~4문장)", "normal": "보통 (5~7문장)", "long": "자세히"}
# 이미지 AI 제공사 · 영상 모드 (Higgsfield는 HIGGSFIELD_API_KEY가 있을 때만 실제 호출)
IMAGE_PROVIDERS = {"openai": "OpenAI", "higgsfield": "Higgsfield (Marketing Studio)"}
VIDEO_MODES = {"plan_only": "구성안만 (실제 영상 아님)", "higgsfield": "실제 영상 (Higgsfield Seedance 2.0 · 720p)"}

GOAL_PRESETS = ("브랜드 알리기", "제품 소개", "구매 유도", "재구매")


def example_suggestions(field, sheet):
    """키가 없거나 AI 추천이 실패할 때의 예시 추천 3개 (화면에 '예시 추천'으로 표시).

    제작 아이디어일 뿐 시장 분석 결과가 아니며, 성분·가격·효능·수치는 넣지 않습니다.
    """
    ct = sheet.get("content_type") or ""
    products = sheet.get("product_ids") or []
    brand_only = bool(sheet.get("brand_focus")) and not products
    multi = len(products) > 1
    if field == "goal":
        if brand_only:
            items = [("브랜드 첫인상 알리기", "브랜드 자체를 고른 경우 이름과 분위기를 먼저 보여 주기 좋아요"),
                     ("슬로건 'Restart Your Day' · 'Buy Your Tomorrow' 소개", "확인된 슬로건 두 개를 그대로 쓸 수 있어요"),
                     ("브랜드 색(검정 & 네온) 알리기", "시각 콘텐츠에서 브랜드 색을 기억하게 해요")]
        elif ct == "crm":
            items = [("재구매 유도", "이미 구매한 고객에게 다시 떠올리게 하는 메시지예요"),
                     ("신규 구독자 환영", "처음 연결된 고객에게 브랜드를 소개해요"),
                     ("제품 소식 알리기", "선택한 제품의 확인된 정보(형태·구성)만으로 소식을 전해요")]
        elif multi:
            items = [("라인업 함께 소개", "여러 제품을 고른 경우 제품별 정보를 나눠 함께 보여 줘요"),
                     ("브랜드와 제품 연결", "Restart Your Day 메시지와 제품을 이어 줘요"),
                     ("구매 유도", "Buy Your Tomorrow 슬로건과 구매처 안내로 이어져요")]
        else:
            items = [("제품 소개", "선택한 제품의 형태와 맛 후보(임시)를 보여 줘요"),
                     ("브랜드 알리기", "제품을 통해 Reboot 브랜드를 기억하게 해요"),
                     ("구매 유도", "Buy Your Tomorrow 슬로건과 구매처 안내로 이어져요")]
    else:
        if ct == "image":
            items = [("검정 배경에 네온 포인트, 문구는 영어로", "브랜드 색(검정 & 네온)을 바로 보여 줘요"),
                     ("제품을 화면 가운데 크게, 여백 넉넉히" if products else "슬로건이 주인공인 간결한 구성",
                      "한눈에 들어오는 단순한 구성이에요"),
                     ("두 제품을 나란히, 제품별 정보는 따로" if multi else "아침 일상 장면 느낌의 밝은 무드",
                      "여러 제품을 섞지 않고 보여 줘요" if multi else "Restart Your Day와 어울리는 분위기예요")]
        elif ct == "cardnews":
            items = [("5장 구성, 첫 장은 슬로건으로 시작", "훅 → 소개 → 마무리 흐름이 자연스러워요"),
                     ("장마다 문구 한 줄 + 짧은 설명", "모바일에서 읽기 쉬워요"),
                     ("마지막 장에 구매처 안내", "다음 행동을 분명히 해요")]
        elif ct == "shortform":
            items = [("15초 세로형, 영어 자막", "숏폼 기본 길이와 형식이에요"),
                     ("첫 2초 안에 제품이나 슬로건 노출", "초반 이탈을 줄이는 구성이에요"),
                     ("아침 루틴 장면으로 시작", "Restart Your Day 메시지와 이어져요")]
        else:
            items = [("이메일 1통 + SMS 1개, 짧게", "처음엔 작은 단위로 시작해요"),
                     ("제목은 40자 이내", "받은편지함에서 잘리지 않아요"),
                     ("할인·쿠폰 문구 없이", "확인되지 않은 혜택을 만들지 않아요")]
    return [{"value": v, "reason": r} for v, r in items]
