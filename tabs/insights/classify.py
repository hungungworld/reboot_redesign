"""규칙 분류 (AI 없이도 동작) — 리뷰 주제 · 감정, 유튜브 댓글 질문 · 관련 여부, 자주 쓰는 표현.

AI 키가 있으면 '리뷰 분류' · 'VOC 분류' 항목이 이 결과를 AI 결과로 덮어씁니다 (classified_by = ai).
"""
import re
from collections import Counter

TOPICS = ("효과", "맛·향", "복용 편의", "부작용", "가격")
TOPIC_WORDS = {
    "효과": ["work", "works", "worked", "working", "effective", "helped", "helps", "help", "difference", "feel better",
            "felt better", "no hangover", "hangover free", "didn't work", "did not work", "doesn't work", "useless", "placebo",
            "효과", "숙취"],
    "맛·향": ["taste", "tastes", "tasted", "tasty", "flavor", "flavors", "flavour", "sweet", "bitter", "sour", "smell",
             "delicious", "yummy", "gross", "aftertaste", "맛", "향"],
    "복용 편의": ["easy", "convenient", "portable", "carry", "packet", "pouch", "stick", "sticks", "on the go", "texture",
              "swallow", "chew", "chewy", "open", "travel", "간편", "휴대"],
    "부작용": ["side effect", "side effects", "stomach", "nausea", "nauseous", "headache", "diarrhea", "rash", "allergic",
            "allergy", "jittery", "upset", "부작용", "배탈", "두통"],
    "가격": ["price", "expensive", "cheap", "worth", "value", "cost", "pricey", "overpriced", "money", "deal", "가격",
           "비싸", "저렴"],
}
POS_WORDS = ["love", "loved", "great", "good", "amazing", "best", "recommend", "delicious", "perfect", "excellent", "awesome",
             "works great", "life saver", "lifesaver", "좋", "추천"]
NEG_WORDS = ["bad", "terrible", "awful", "didn't work", "did not work", "doesn't work", "waste", "gross", "disgusting",
             "horrible", "worst", "useless", "broken", "overpriced", "never again", "refund", "별로", "최악"]


def _pattern(words):
    alts = []
    for w in words:
        if re.search(r"[A-Za-z]", w):
            alts.append(r"(?<![A-Za-z])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![A-Za-z])")
        else:
            alts.append(re.escape(w))
    return re.compile("|".join(alts), re.I)


_TOPIC_RE = {t: _pattern(ws) for t, ws in TOPIC_WORDS.items()}
_POS_RE = _pattern(POS_WORDS)
_NEG_RE = _pattern(NEG_WORDS)


def review_topics(text):
    t = str(text or "")
    return [topic for topic in TOPICS if _TOPIC_RE[topic].search(t)]


def review_sentiment(text, rating=None):
    """별점이 있으면 별점(4~5 칭찬 · 1~2 불만 · 3 중립), 없으면 단어로."""
    try:
        r = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        r = None
    if r is not None:
        return "+" if r >= 4 else "-" if r <= 2 else "0"
    t = str(text or "")
    pos, neg = len(_POS_RE.findall(t)), len(_NEG_RE.findall(t))
    return "+" if pos > neg else "-" if neg > pos else "0"


# ───────── 유튜브 댓글 ─────────

_Q_START = re.compile(r"^\s*(how|what|where|when|why|which|who|does|do|is|are|can|could|should|would|will|did|has|have|any|anyone)\b", re.I)
QUESTION_TOPICS = [
    ("compare", "다른 제품과 비교", [" vs", "versus", "better than", "instead of", "compared to", "compare", "difference between"]),
    ("buy", "어디서 사나요 (구매처)", ["where", "buy", "purchase", "available", "amazon", "store", "walmart", "target", "costco",
                                    "link", "order", "ship", "sell", "find"]),
    ("works", "정말 효과가 있나요", ["work", "works", "effective", "legit", "actually", "really help", "does it help", "result"]),
    ("when", "언제 · 얼마나 먹나요", ["when", "before", "after", "how many", "how much", "dose", "dosage", "take it", "drink it",
                                  "should i take"]),
    ("safety", "안전한가요 · 부작용", ["safe", "side effect", "liver", "kidney", "pregnan", "allerg", "medication", "health"]),
    ("taste", "맛이 어떤가요", ["taste", "flavor", "flavour", "sweet", "bitter"]),
    ("price", "가격 · 가성비", ["price", "cost", "expensive", "cheap", "worth"]),
    ("ingredients", "무엇이 들어 있나요 (성분)", ["ingredient", "contain", "what's in", "whats in", "made of", "sugar", "caffeine",
                                         "vegan"]),
]
QUESTION_LABEL = {k: label for k, label, _ in QUESTION_TOPICS}
# 숙취 공감 · 한탄 · 농담 — 제품 이야기가 아님
NOISE = ["anyone else", "who else", "never drinking again", "so hungover", "lol", "lmao"]
# 해결책 · 제품 단어 — '숙취' · '술'만 나오는 잡담은 통과시키지 않음
SOLUTION_WORDS = ["cure", "remedy", "supplement", "jelly", "shot", "pill", "tablet", "capsule", "stick", "packet", "electrolyte",
                  "dhm", "product", "brand", "this drink", "drink it", "tried", "try it", "taste", "flavor", "price", "buy", "korean",
                  "숙취해소", "효과"]
# 영상 제목 — 해결책 · 제품 · 리뷰 영상만 댓글을 모음 (설정의 자사 이름도 함께 봄)
VIDEO_WORDS = ["cure", "remedy", "supplement", "jelly", "shot", "pill", "electrolyte", "dhm", "drink", "drinks", "review",
               "tested", "tried", "vs", "best", "korean", "recovery", "숙취해소"]
MIN_WORDS = 5

_NOISE_RE = _pattern(NOISE)
_SOLUTION_RE = _pattern(SOLUTION_WORDS)


def is_question(text):
    """질문 모양(? · 의문사)이면서 구매 전 궁금증 주제에 걸려야 질문."""
    t = str(text or "").strip()
    return ("?" in t or bool(_Q_START.match(t))) and bool(question_topic(t))


def question_topic(text):
    t = str(text or "").lower()
    for key, _label, words in QUESTION_TOPICS:
        if any(w in t for w in words):
            return key
    return ""


def is_relevant(text):
    """공감 · 잡담 · 유튜버 칭찬은 빼고, 해결책 · 제품 이야기이거나 제품 질문인 댓글만 (규칙 · 대략)."""
    t = str(text or "")
    if len(t.split()) < MIN_WORDS or _NOISE_RE.search(t):
        return False
    return bool(_SOLUTION_RE.search(t)) or is_question(t)


def video_on_topic(title, extra=()):
    """영상 제목에 해결책 · 제품 · 리뷰 단어(또는 자사 이름)가 있는지."""
    return bool(_pattern(list(VIDEO_WORDS) + [w for w in extra if w]).search(str(title or "")))


def classify_comment(text):
    rel = is_relevant(text)
    q = rel and is_question(text)
    return {"relevant": rel, "is_question": q, "topic": question_topic(text) if q else ""}


STOP = set("""a an the and or but if so to of in on at for with from by as is it its it's this that these those i i'm im you your
you're we they them he she my me our us be been being was were are am do does did done have has had having just not no yes
very really like get got can could will would should one all any some more most much many what when where why how who which
there here than then too also only even still about into out up down over again ever lol omg haha video thanks thank please
make made know think want need going go gonna u ur""".split())


def expressions(texts, top=5):
    """자주 쓰는 표현 (2~3단어 묶음 · 몇 개 댓글에 나왔나). 불용어만으로 된 묶음은 뺌."""
    counts, example = Counter(), {}
    for text in texts:
        words = re.findall(r"[a-z][a-z']+", str(text or "").lower())
        grams = set()
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                g = words[i:i + n]
                if g[0] in STOP or g[-1] in STOP or all(w in STOP for w in g):
                    continue
                grams.add(" ".join(g))
        for g in grams:
            counts[g] += 1
            example.setdefault(g, str(text)[:200])
    out = []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0].count(" "), kv[0]))  # 같은 횟수면 긴 표현 먼저
    for phrase, c in ranked[: top * 6]:
        if c < 2:
            break
        if any(phrase in o["phrase"] or o["phrase"] in phrase for o in out):  # 겹치는 묶음은 하나만
            continue
        out.append({"phrase": phrase, "count": c, "example": example[phrase]})
        if len(out) >= top:
            break
    return out
