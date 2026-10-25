"""사용자 발화에서 서버가 직접 판단하는 신호 (설명서 5.2·5.3).

모델이 generate라고 답했다는 이유만으로 실행하지 않습니다. 서버는 사용자 발화(또는 버튼)에
명확한 생성·수정 요청이 있을 때만 실행하고, "좋네" 같은 감상은 확정·생성으로 이어지지 않게 합니다.
"""
import re

# 감상·맞장구만 있는 말 (문장 전체가 이것뿐일 때)
ACK_RE = re.compile(
    r"^\s*(좋네요?|좋아요?|좋다|좋습니다|좋은데요?|괜찮네요?|괜찮아요?|괜찮다|괜찮은데요?|오케이|오키|ok(ay)?|ㅇㅋ|굿|굳|"
    r"nice|great|cool|good|멋지네요?|멋져요?|멋있네요?|훌륭해요?|훌륭하네요?|마음에\s*들어요?|맘에\s*들어요?|그렇네요?|"
    r"그래요?|그렇군요|네|넹|응|ㅇㅇ|넵|예|감사합니다|고마워요?|땡큐|thanks?|thank you|와+|오+|대박)"
    r"[\s!.~?ㅎㅋ👍]*$", re.I)

CREATE_RE = re.compile(
    r"(만들어|만들자|만들래|만들기\s*시작|생성\s*(해|하|좀|시작|요청)|작성\s*(해|하|좀|부탁)|써\s*(줘|주세요|봐|줄래)|뽑아|그려|"
    r"제작\s*(해|하|시작)|초안\s*(을|좀)?\s*(줘|부탁|보여)|시안\s*(을|좀)?\s*(줘|보여|부탁)|다시\s*(만들|써|뽑|그려)|"
    r"하나\s*더|더\s*만들|\b(create|generate|make|write|draft|redo|rewrite|render)\b)", re.I)
GO_RE = re.compile(r"((바로|지금|먼저|우선)\s*(진행|시작|가자|가|해|만들|작성|ㄱ)|진행\s*해|시작\s*해|고고|ㄱㄱ|\bgo\b)", re.I)
NOUN_FIRST_RE = re.compile(
    r"(카피|문구|캡션|초안|기획서|기획안|대본|구성안|시안|이미지|카드뉴스|메시지|이메일|문자|SMS|설명|상세)\s*(만|부터|도|를|을)?\s*"
    r"(먼저|부터|바로|우선)", re.I)
NOUN_ONLY_END_RE = re.compile(r"(카피|문구|캡션|초안|대본|구성안|기획서|설명|메시지)\s*만\s*[.!~]*\s*$")
EDIT_RE = re.compile(
    r"(수정|바꿔|바꿔줘|바꿔\s*줘|바꾸자|바꿀래|변경\s*해|고쳐|줄여|늘려|짧게|길게|간결하게|다듬|교체|키워|작게|크게|밝게|어둡게|"
    r"강조\s*해|\b(shorter|longer|change|edit|replace|fix|tweak)\b)", re.I)
ONLY_TO_RE = re.compile(r"\S+\s*만\s*\S+(으로|로)(\s*(해|해줘|바꿔|변경|가자))?\s*[.!~]*\s*$")
QUESTION_RE = re.compile(r"(왜|어떻게|어떤\s*(이유|방식|근거)|무슨\s*(이유|뜻)|뭐가\s*(좋|나아|달라)|이유가|차이가)")
EXCLUDE_RE = re.compile(
    r"(이미지|사진|그림|비주얼|영상|비디오|동영상)\s*(는|은|도)?\s*(말고|빼고|없이|제외|필요\s*없|안\s*해도|하지\s*마|만들지\s*마)")
VERSION_REF_RE = re.compile(r"([A-Z])\s*안|시안\s*(\d+)|\bv(\d+)\b", re.I)

# 부분 수정 대상: 카드뉴스 장 · 숏폼 장면
SLIDE_LIST_RE = re.compile(r"(\d{1,2}(?:\s*[,·와과및]\s*\d{1,2})*)\s*(?:번째\s*)?장\s*(?=만|의|을|를|에서|이랑|하고|과|와|,|문구|제목|배경|이미지)")
SLIDE_ID_RE = re.compile(r"\bS(\d{1,2})\b")
SCENE_RE = re.compile(r"장면\s*(\d{1,2})|(\d{1,2})\s*번\s*장면|(\d{1,2})\s*번째\s*장면|\bSC\s*(\d{1,2})\b", re.I)


def is_vague_ack(text):
    return bool(ACK_RE.match(text or ""))


# 만들기 제안("이대로 만들까요?") 바로 다음의 짧은 동의 — 제안이 있을 때만 실행으로 봄
AGREE_RE = re.compile(
    r"(좋아|좋네|좋습니다|괜찮|오케이|오키|ok(ay)?|ㅇㅋ|ㅇㅇ|응|네|넹|넵|예|그래|그렇게|콜|고고|ㄱㄱ|진행|만들어|해\s*줘|해\s*주세요|"
    r"부탁|가자|\bgo\b|\byes\b|sure)", re.I)
DISAGREE_RE = re.compile(r"(아니|아뇨|잠깐|잠시만|말고|아직|다시\s*생각|바꾸|바꿔|수정|빼고|대신|근데|그런데|\?|？|\bno\b)", re.I)
REFERENCE_RE = re.compile(r"(레퍼런스|레퍼|참고\s*(이미지|사진|자료)|올린\s*(이미지|사진)|첨부|reference)", re.I)


def is_agreement(text):
    """'좋아요', '네 만들어줘', 'ㄱㄱ' 같은 짧은 동의 (수정·질문이 섞이면 아님)."""
    t = (text or "").strip()
    return bool(t) and len(t) <= 40 and bool(AGREE_RE.search(t)) and not DISAGREE_RE.search(t)


def mentions_reference(text):
    return bool(REFERENCE_RE.search(text or ""))


def excluded_media(text):
    out = set()
    for m in EXCLUDE_RE.finditer(text or ""):
        word = m.group(1)
        out.add("video" if word in ("영상", "비디오", "동영상") else "image")
    return sorted(out)


def version_refs(text):
    refs = []
    for m in VERSION_REF_RE.finditer(text or ""):
        if m.group(1):
            refs.append({"label": f"{m.group(1).upper()}안"})
        elif m.group(2):
            refs.append({"label": f"시안 {int(m.group(2))}"})
        elif m.group(3):
            refs.append({"seq": int(m.group(3))})
    return refs


def unit_targets(text):
    """'2장만 수정' → slides [2] · '장면 3' → scenes [3]. 대상이 없으면 빈 목록."""
    text = text or ""
    slides, scenes = set(), set()
    for m in SLIDE_LIST_RE.finditer(text):
        slides.update(int(n) for n in re.findall(r"\d{1,2}", m.group(1)))
    slides.update(int(n) for n in SLIDE_ID_RE.findall(text))
    for m in SCENE_RE.finditer(text):
        n = next(g for g in m.groups() if g)
        scenes.add(int(n))
    return {"slides": sorted(n for n in slides if 0 < n <= 30), "scenes": sorted(n for n in scenes if 0 < n <= 30)}


def is_explicit_request(text, has_base=False):
    """명확한 생성·수정 요청인가 (설명서 5.3). 질문·감상은 아님."""
    t = (text or "").strip()
    if not t or is_vague_ack(t):
        return False
    question = bool(QUESTION_RE.search(t))
    if (CREATE_RE.search(t) or GO_RE.search(t) or NOUN_FIRST_RE.search(t) or NOUN_ONLY_END_RE.search(t)) and not question:
        return True
    refers = bool(VERSION_REF_RE.search(t))
    if EDIT_RE.search(t) and (has_base or refers) and not question:
        return True
    if has_base and ONLY_TO_RE.search(t) and not question:
        return True
    return False


def signals(text, *, has_base=False, force=None, kickoff=False):
    """서버 신호 묶음 — 모델에도 참고로 주고, 실행 여부는 서버가 이 값으로 정함."""
    t = text or ""
    return {
        "kickoff": bool(kickoff),
        "vague_ack": is_vague_ack(t),
        "explicit_request": bool(force) or (not kickoff and is_explicit_request(t, has_base)),
        "question": bool(QUESTION_RE.search(t)) or t.strip().endswith("?"),
        "excluded_media": excluded_media(t),
        "version_refs": version_refs(t),
        "unit_targets": unit_targets(t),
        "force_action": force or None,
    }
