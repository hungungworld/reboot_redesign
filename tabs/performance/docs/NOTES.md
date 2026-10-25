# 성과분석 — 구현 메모 (v1)

설명서는 [README.md](README.md). 이 문서는 Flask 대시보드에 실제로 들어간 것만 짧게 적습니다.
README 7장의 주소는 공용 주소와 겹치지 않게 `/api/performance/…` 아래로 옮겼습니다 (`/data/performance.csv` → `GET /api/performance/data`, `/api/fx` → `GET /api/performance/fx`, `/api/openai` → `POST /api/performance/advice`).

## 구현됨

| 영역 | 내용 |
|---|---|
| 데이터 | 업로드한 CSV(`DATA_DIR/uploads/performance/performance.csv`) → 없으면 저장소 샘플 `tabs/performance/data/performance_sample.csv` (2026-09-01 ~ 10-21 · Instagram·TikTok·Amazon·Google · **가상값**, 화면·보고서에 "샘플 데이터" 표시) |
| 업로드 | 합치기(같은 날짜+채널은 새 값) · 전체 바꾸기. 헤더 순서 검사, `2026/10/19` · `2026.10.19` · `"1,260,000"` · `1260000원`, 엑셀 EUC-KR. 덮어쓰기 전 `DATA_DIR/backups/performance-YYYYMMDD-HHMMSS.csv`. 샘플과는 섞지 않음. "샘플로 되돌리기"(백업 후 삭제) |
| 화면 (README 1장 순서) | 기간(달력·최근 7일) → 기본 정보 → 핵심 지표 6 (CPA는 낮을수록 초록, 광고비 증감은 회색) → 채널별 표 (원화/달러 · 환율 확인 · 행 클릭으로 채널 선택) → 막대 · 선 차트 (범례 클릭으로 숨기기) → 잘한 점 / 개선할 점 (숫자 기준 → AI) → AI 제안 · 다음 주 예산 |
| 계산 | `tabs/performance/static/metrics.js`(화면) = `tabs/performance/metrics.py`(서버). 비교 기간은 같은 길이 바로 앞, CSV 시작일 이전이면 "데이터 없음". 합계 비율은 합계로 다시 계산 |
| 환율 | 수출입은행 AP01 USD 매매기준율, 주말·공휴일은 7일까지 거슬러 찾음, 성공 값은 `DATA_DIR/cache/performance-fx.json`. 키 없음·실패 → 1,400원 "기본값 (이유)" |
| AI | README 5장 프롬프트(10년 경력 컨설턴트 · 퍼널 · 주어진 숫자만 · 다가오는 미국 시즌), strict JSON. 예산 총액은 서버가 기준 광고비 ±20% 안으로 맞춤(채널 비율 유지 · 1,000원 단위 · 빠진 채널은 유지 · 없는 채널은 버림). 7일이 아닌 기간은 7일 기준으로 환산. 같은 탭·같은 날·같은 기간은 다시 부르지 않음(sessionStorage), "다시 만들기" |
| 보고서 | `/dashboard/performance?view=report&start=…&end=…` — A4 문서, 기간 목록(직접 지정 · 최근 7일 · 월~일 주), 인쇄 → PDF |
| 공개 | `REBOOT.use('performance').weekSummary()` · `summary({start, end})` · 계산 함수 `REBOOT.use('performance.metrics')` |

## 쓰는 법 · 확인

- 키: `OPENAI_API_KEY`(AI 해석·예산), `KEXIM_API_KEY`(환율) — 설정 › 연결. 없어도 화면은 숫자 기준 문구 · 1,400원으로 동작
- 테스트: `node tabs/performance/tests/perf_metrics.test.js` (계산) · `pytest tabs/performance/tests/test_performance.py` (API, 인터넷 없이)
- 샘플 다시 만들기가 필요하면 같은 규칙(날짜 · 채널 · 헤더)으로 CSV를 바꾸면 됩니다 (가상값임을 유지)

## 아직 안 됨

- 마진 계산기 이관(팀 README 16장 #4) · 파트너 `creatorSales()` 연동
- 달러 표시는 채널 표에만 (README대로 지표 · 차트 · 보고서는 원화)
- Render 무료 플랜은 재배포 때 업로드 파일이 사라짐 → Persistent Disk + `DATA_DIR` 필요
