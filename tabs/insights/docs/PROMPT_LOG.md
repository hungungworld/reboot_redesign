# 프롬프트 로그 — 유튜브 댓글 VOC 분류 (퓨샷 · 판단 근거)

담당: HK (현경) · 탭: 시장·고객 인사이트(`tabs/insights/`) · 작성: 2026-10-25

## 1. 무엇을 하는 프롬프트인가

미국 유튜브 숙취 영상의 댓글을 읽고 세 가지를 판단해요.

| 칸 | 값 | 뜻 |
|---|---|---|
| `relevant` | true / false | 숙취해소 **제품 · 해결책** 이야기인가 (한탄 · 농담 · 유튜버 칭찬은 false) |
| `question` | true / false | 제품을 **사기 전 궁금증**을 묻는 질문인가 |
| `topic` | compare · buy · works · when · safety · taste · price · ingredients · none | 질문이면 주제 |

결과는 월간 리뷰 › 고객 인사이트의 "사기 전에 묻는 것(FAQ)"과 데이터 › 유튜브 댓글 화면에 쓰여요.

## 2. 적용한 프롬프트 기법

| 기법 | 어디에 | 코드 |
|---|---|---|
| **Few-shot** | 사람이 정답을 붙인 댓글 24건을 `댓글 → 답` 형태로 시스템 프롬프트에 넣음. 헷갈리기 쉬운 경우(물음표가 있지만 질문이 아님, buy가 있지만 무관, 해결책이 효과 없었다는 평가)를 일부러 골랐어요 | `tabs/insights/ai.py` `classify_comments` · `_few_shot_text` |
| **CoT (판단 근거 먼저)** | 답 칸보다 `reason`(한국어 한 문장)을 먼저 쓰게 함. 예시에도 근거를 붙여 근거 → 답 순서를 보여 줌 | `COMMENT_SCHEMA`의 칸 순서 (`id` → `reason` → 답) |
| **JSON 출력 고정** | JSON Schema(strict)로 칸 · 값 목록(enum)을 고정 → 파싱 실패 · 없는 주제를 막음 | `COMMENT_SCHEMA` · `server/core/ai.py` `openai_json` |
| **규칙 후처리** | 관련 없으면 질문 아님, 주제를 모르면 질문 아님 | `tabs/insights/update.py` `_run_voc` · `accuracy._norm` |

- 모델: OpenAI (`.env`의 `OPENAI_MODEL`, 없으면 `gpt-4o-mini`). 실제 쓴 모델은 6번 채점 결과에 적어요. 팀 규칙(RULE.md §8)상 인사이트 탭은 OpenAI를 써요. 수업 평가표는 Gemini를 예로 들고 있어요.
- 한 번에 댓글 80건(VOC 분류) / 40건(정확도 검증)씩 보내요.

## 3. 실제 시스템 프롬프트 (예시 포함)

사용자 메시지는 `{"comments": [{"id": "...", "text": "..."}]}` 형태예요.

```text
유튜브 댓글이 숙취해소 제품 · 해결책과 관련 있는지(relevant), 제품을 사기 전 궁금증을 묻는 질문인지(question) 판단하고 질문이면 주제(topic)를 골라: compare=다른 제품과 비교, buy=어디서 사나요 (구매처), works=정말 효과가 있나요, when=언제 · 얼마나 먹나요, safety=안전한가요 · 부작용, taste=맛이 어떤가요, price=가격 · 가성비, ingredients=무엇이 들어 있나요 (성분). 어느 주제에도 맞지 않으면 question=false, topic=none. 숙취 공감 · 한탄 · 농담('anyone else hungover?' 등), 유튜버 칭찬 · 잡담 · 광고는 relevant=false. relevant=false면 question=false, topic=none. 각 댓글마다 먼저 reason에 판단 근거를 한국어 한 문장(20자 안팎)으로 쓰고, 그 근거에 맞게 나머지 칸을 채워.

사람이 정답을 붙인 예시 (댓글 → 답):
- "I swear i will never ever drink again" → {"reason": "다시는 안 마시겠다는 한탄, 해결책 이야기 없음", "relevant": false, "question": false, "topic": "none"}
- "Omg you look so pretty the glasses look good on you It's a compliment ☺️❤" → {"reason": "출연자 외모 칭찬", "relevant": false, "question": false, "topic": "none"}
- "Or... Gatorade/powerade. The only cure is hydration and time." → {"reason": "이온음료 · 수분 보충이 해결책이라는 의견, 질문 아님", "relevant": true, "question": false, "topic": "none"}
- "These were NOT cures man. Preventative advice doesn't help me, now I'm hungover and educated about what I could have done last night" → {"reason": "영상 속 해결책이 도움 안 됐다는 평가 → 해결책 이야기라 관련", "relevant": true, "question": false, "topic": "none"}
- "Who else has the opposite relationship with their dad?" → {"reason": "가족 이야기 잡담, 질문이어도 관련 없음", "relevant": false, "question": false, "topic": "none"}
- "What are the products’ main purpose? For example, most ‘anti hangover supplements’ in Denmark contains mostly electrolytes to restore hydration or salt/sugar combo to restore your body’s salt/sugar levels" → {"reason": "숙취해소 보조제가 무엇으로 만들어졌는지 묻는 질문 → 성분", "relevant": true, "question": true, "topic": "ingredients"}
- "A tall glass of water with ice and aspirin works for me." → {"reason": "물 · 아스피린이 효과 있었다는 해결책 경험, 질문 아님", "relevant": true, "question": false, "topic": "none"}
- "I remeber hearing [name] say something to the effect that bad/cheap tequila cause hangovers more than good stuff. and i have also hear that pure alcohols like vodka cause less hangovers. is that true?" → {"reason": "싼 술이 숙취를 더 일으킨다는 말이 사실인지 묻는 질문 → 효과", "relevant": true, "question": true, "topic": "works"}
- "Wat about vitamin b before drinking?" → {"reason": "술 마시기 전에 비타민 B를 먹는 게 어떤지 묻는 질문 → 복용 시점", "relevant": true, "question": true, "topic": "when"}
- "have to be randomized double blind placebo controled to be sure it works ask nutritionfacts" → {"reason": "효과를 믿으려면 임상시험이 필요하다는 효과 의견", "relevant": true, "question": false, "topic": "none"}
- "I'm So Hungover Right Now, I'm Never Drinking Again" → {"reason": "숙취 상태 한탄", "relevant": false, "question": false, "topic": "none"}
- "Amla powder drinked in water is a extremely healthy hangover KILLER! 🤗❤️🌱" → {"reason": "암라 가루가 숙취에 좋다는 해결책 추천", "relevant": true, "question": false, "topic": "none"}
- "\"I'm hungover\" \"Let me say a bunch of words at you and then use a blender next to your head\"" → {"reason": "영상 장면을 흉내 낸 농담", "relevant": false, "question": false, "topic": "none"}
- "What about electrolytes doc? I try to drink an all glass of water mixed with electrolytes the night before and it helps! I also find helpful lemon water in the morning and juice/fruit smoothie to bring my sugar levels back up." → {"reason": "전해질이 숙취에 도움이 되는지 묻는 질문 → 성분", "relevant": true, "question": true, "topic": "ingredients"}
- "Where was this 12 hours ago, guys??" → {"reason": "해결책 영상을 더 일찍 봤어야 했다는 반응, 물음표가 있어도 궁금증 아님", "relevant": true, "question": false, "topic": "none"}
- "I think they should put a disclaimer that you shouldn't take paracetamol with alcohol" → {"reason": "술과 진통제를 함께 먹지 말라는 안전 의견, 질문 아님", "relevant": true, "question": false, "topic": "none"}
- "Weird question why do you say zed instead of z ?" → {"reason": "말투에 대한 질문, 제품 · 해결책과 무관", "relevant": false, "question": false, "topic": "none"}
- "Thank you, I drink ACV, it contains 5% alcohol. Is that a problem?" → {"reason": "알코올이 든 식초 음료를 먹어도 되는지 묻는 질문 → 안전", "relevant": true, "question": true, "topic": "safety"}
- "I have never tried cow blood, but have had pig blood several times. I would love to try this next time im in korea." → {"reason": "선지 음식 경험담, 숙취 해결책 아님", "relevant": false, "question": false, "topic": "none"}
- "Beer + Paracetamol AKA Acetaminophen BKA Tylenol damages the liver." → {"reason": "진통제와 술이 간에 해롭다는 안전 정보", "relevant": true, "question": false, "topic": "none"}
- "CAN YOU BUY GELATO 🍦IT WAS VERY GOOD 👍" → {"reason": "buy가 들어가도 젤라토 이야기, 숙취와 무관", "relevant": false, "question": false, "topic": "none"}
- "Можно подержать голову в воде 10 минут и тогда похмелье не будет мучить никогда" → {"reason": "머리를 물에 담그라는 농담, 해결책으로 보기 어려움", "relevant": false, "question": false, "topic": "none"}
- "1st- 6/10- I hate beer- beer and ibuprofen nah. 2nd- 9/10 standard. 3rd 10/10- ramen and electrolytes. 4th- I wasn’t able to taste- 8/10." → {"reason": "영상 속 해결책들을 점수로 매긴 후기", "relevant": true, "question": false, "topic": "none"}
- "Is it bad that my liver hurts?" → {"reason": "간이 아픈데 괜찮은지 묻는 건강 질문 → 안전", "relevant": true, "question": true, "topic": "safety"}
```

## 4. 정답 데이터와 나누기

- 파일: `tabs/insights/data/comment_examples.json` (100건)
- 출처: 시연 DB의 유튜브 댓글 2,838건에서 고루 뽑은 100건 (질문 30 · 관련 38 · 무관 32 후보). 작성자 이름 · 링크 · 출연자 이름은 지웠어요.
- 정답: HK 확정 (초안은 Gemini로 작성 후 HK가 검토 · 수정). 예시 24건의 `reason`은 Claude Code가 작성했어요.

| 구분 | 건수 | 관련 | 질문 | 질문 주제 |
|---|---|---|---|---|
| 예시 (프롬프트에 넣음) | 24 | 15 | 6 | 성분 2 · 효과 1 · 안전 2 · 복용 시점 1 |
| 채점용 (프롬프트에 안 넣음) | 76 | 50 | 9 | 성분 4 · 효과 4 · 안전 1 |

- 예시와 채점용은 **겹치지 않아요** (테스트 `test_classify_comments_few_shot_prompt`가 확인).
- 구매처 · 가격 · 맛 · 비교 질문은 0건이라 그 주제의 정확도는 말할 수 없어요.

## 5. 정확도 검증 방법

데이터 › 정확도 검증 › **채점 실행** (데이터 업데이트 창의 '정확도 검증' 항목, OpenAI 4회)

1. 채점용 76건을 세 방식으로 분류: **규칙**(단어 목록, `classify.py`) / **AI 예시 없이**(같은 프롬프트에서 예시만 뺌) / **AI 예시 포함**(퓨샷)
2. 정답과 비교해 맞은 비율: 관련 여부 · 질문 여부 · 질문 주제 · **세 칸 모두**
3. 결과는 `insights_kv`의 `accuracy`에 저장되고, 화면에 비교표와 틀린 댓글 목록이 보여요

## 6. 채점 결과

> **아직 실행 전이에요.** 채점 실행 후 화면의 숫자를 그대로 옮겨 적어요. (지어낸 숫자 금지)

| 방식 | 관련 여부 | 질문 여부 | 질문 주제 | 세 칸 모두 | 틀린 댓글 |
|---|---|---|---|---|---|
| 규칙 (단어) | | | | | |
| AI · 예시 없이 | | | | | |
| AI · 예시 포함 (퓨샷) | | | | | |

실행 일시 · 모델:

## 7. 호출 기록 (작업 로그)

- AI를 부를 때마다 `server/core/ai.py`가 작업 로그에 **AI 프롬프트** 항목으로 남겨요: 모델 · 토큰 수 · 프롬프트 · 응답.
  - VOC 분류: `VOC(유튜브 댓글) 분류`
  - 비교용: `VOC(유튜브 댓글) 분류 · 예시 없이 (정확도 검증)`
- 작업 로그의 프롬프트는 6,000자에서 잘려요. 이 프롬프트는 예시 때문에 약 6,600자라서, 전체 원문은 위 3번을 보세요.

## 8. AI 도구 사용

- 정답 초안: Gemini → HK 검토 · 확정
- 예시 고르기 · 판단 근거 문장 · 코드 · 테스트 · 이 문서: Claude Code (HK 승인 후 작성)
