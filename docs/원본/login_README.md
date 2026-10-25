# 로그인 화면 명세 (Flask 대시보드)

## 1. 개요

대시보드에 접근하기 위한 첫 화면입니다. 인증되지 않은 사용자는 모든 대시보드 페이지에서 로그인 화면으로 리다이렉트됩니다.

- **URL**: `/login`
- **접근 권한**: 비로그인 사용자 (이미 로그인된 사용자는 `/dashboard`로 리다이렉트)
- **로그인 성공 시**: `next` 파라미터가 있으면 해당 페이지로, 없으면 `/dashboard`로 이동
- **로그아웃 URL**: `/logout` (POST)

---

## 2. 화면 구성

```
┌──────────────────────────────────────┐
│               [로고]                  │
│          대시보드 로그인               │
│                                      │
│  ┌────────────────────────────────┐  │
│  │ 이메일                          │  │
│  └────────────────────────────────┘  │
│  ┌────────────────────────────────┐  │
│  │ 비밀번호                    👁  │  │
│  └────────────────────────────────┘  │
│                                      │
│  ☐ 로그인 상태 유지     비밀번호 찾기  │
│                                      │
│  ┌────────────────────────────────┐  │
│  │            로그인               │  │
│  └────────────────────────────────┘  │
│                                      │
│  [에러 메시지 영역]                   │
└──────────────────────────────────────┘
```

### 구성 요소

| 요소 | 타입 | 설명 |
|---|---|---|
| 로고 / 타이틀 | 이미지, 텍스트 | 서비스 로고와 "대시보드 로그인" 문구 |
| 이메일 | `input[type=email]` | 필수, 자동완성 `username` |
| 비밀번호 | `input[type=password]` | 필수, 자동완성 `current-password`, 보기/숨기기 토글 |
| 로그인 상태 유지 | `checkbox` | 체크 시 세션 유지 기간 연장 (예: 14일) |
| 비밀번호 찾기 | 링크 | `/forgot-password` (추후 구현) |
| 로그인 버튼 | `button[type=submit]` | 제출 중에는 비활성화 + 로딩 표시 |
| 메시지 영역 | flash 메시지 | 에러/안내 메시지 표시 |

### 디자인 가이드

- 화면 중앙 정렬 카드 레이아웃 (최대 너비 400px)
- 모바일 대응 (좌우 여백 16px)
- 에러 메시지는 빨간색 계열, 안내 메시지는 회색/파란색 계열
- 다크 모드 고려 (선택)

---

## 3. 입력 검증

### 클라이언트 측
- 이메일 형식 확인
- 빈 값 제출 방지 (`required`)

### 서버 측
| 항목 | 규칙 | 에러 메시지 |
|---|---|---|
| 이메일 | 필수, 이메일 형식, 최대 255자 | "올바른 이메일을 입력해 주세요." |
| 비밀번호 | 필수, 최대 128자 | "비밀번호를 입력해 주세요." |
| 인증 실패 | 이메일 또는 비밀번호 불일치 | "이메일 또는 비밀번호가 올바르지 않습니다." |
| 계정 잠금 | 연속 실패 5회 초과 | "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요." |
| 비활성 계정 | `is_active = False` | "비활성화된 계정입니다. 관리자에게 문의하세요." |

> 보안상 "이메일이 존재하지 않음"과 "비밀번호 틀림"을 구분하지 않고 같은 메시지를 보여줍니다.

---

## 4. 처리 흐름

```
[GET /login]
   └─ 로그인 상태? ── 예 ──▶ /dashboard 리다이렉트
           │
          아니오
           ▼
     로그인 폼 렌더링

[POST /login]
   ├─ CSRF 토큰 검증 실패 ──▶ 400
   ├─ 폼 검증 실패 ──▶ 폼 재렌더링 + 에러 메시지
   ├─ 사용자 조회 + 비밀번호 해시 비교
   │     ├─ 실패 ──▶ 실패 횟수 증가, 에러 메시지
   │     └─ 성공
   │           ├─ 비활성 계정 ──▶ 에러 메시지
   │           └─ login_user(user, remember=체크여부)
   │                 └─ 실패 횟수 초기화, 마지막 로그인 시각 기록
   └─ next 파라미터 검증 후 리다이렉트
```

---

## 5. 라우트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/login` | 로그인 화면 표시 |
| POST | `/login` | 로그인 처리 |
| POST | `/logout` | 로그아웃 후 `/login`으로 이동 |

---

## 6. 데이터 모델 (User)

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | Integer, PK | |
| `email` | String(255), unique | 로그인 ID |
| `password_hash` | String(255) | `werkzeug.security` 해시 |
| `name` | String(100) | 표시 이름 |
| `is_active` | Boolean | 계정 활성 여부 |
| `failed_login_count` | Integer | 연속 실패 횟수 |
| `locked_until` | DateTime, nullable | 잠금 해제 시각 |
| `last_login_at` | DateTime, nullable | 마지막 로그인 시각 |
| `created_at` | DateTime | 생성 시각 |

---

## 7. 보안 요구사항

- **비밀번호 해시**: `werkzeug.security.generate_password_hash` / `check_password_hash` 사용 (평문 저장 금지)
- **CSRF 보호**: Flask-WTF의 `CSRFProtect` 적용
- **세션 쿠키 설정**
  - `SESSION_COOKIE_HTTPONLY = True`
  - `SESSION_COOKIE_SECURE = True` (운영 환경, HTTPS)
  - `SESSION_COOKIE_SAMESITE = "Lax"`
  - `REMEMBER_COOKIE_DURATION = timedelta(days=14)`
- **SECRET_KEY**: 환경변수로 관리 (`.env`, 코드에 하드코딩 금지)
- **오픈 리다이렉트 방지**: `next` 파라미터는 같은 호스트의 상대 경로만 허용
- **무차별 대입 방지**: 계정별 실패 횟수 제한 + (선택) Flask-Limiter로 IP당 요청 제한 (예: 분당 10회)
- **세션 고정 방지**: 로그인 성공 시 세션 갱신
- **로그아웃은 POST**로 처리 (CSRF로 인한 강제 로그아웃 방지)

---

## 8. 사용 라이브러리

```
Flask
Flask-Login        # 세션/인증 관리
Flask-WTF          # 폼 + CSRF
Flask-SQLAlchemy   # DB
Flask-Limiter      # 요청 제한 (선택)
python-dotenv      # 환경변수
email-validator    # WTForms 이메일 검증
```

---

## 9. 디렉터리 구조 (예시)

```
dashboard/
├── app/
│   ├── __init__.py          # create_app, 확장 초기화
│   ├── models.py            # User 모델
│   ├── auth/
│   │   ├── __init__.py      # auth 블루프린트
│   │   ├── forms.py         # LoginForm
│   │   └── routes.py        # /login, /logout
│   ├── dashboard/
│   │   └── routes.py        # @login_required 적용
│   ├── templates/
│   │   ├── base.html
│   │   └── auth/
│   │       └── login.html
│   └── static/
│       ├── css/login.css
│       └── js/login.js      # 비밀번호 보기 토글, 중복 제출 방지
├── config.py
├── .env
└── run.py
```

---

## 10. 코드 스케치

### forms.py
```python
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length

class LoginForm(FlaskForm):
    email = StringField("이메일", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("비밀번호", validators=[DataRequired(), Length(max=128)])
    remember = BooleanField("로그인 상태 유지")
    submit = SubmitField("로그인")
```

### routes.py
```python
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user, login_required
from . import auth_bp
from .forms import LoginForm
from ..models import User, db

MAX_FAILS = 5
LOCK_MINUTES = 10

def is_safe_next(target: str | None) -> bool:
    if not target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()

        if user and user.locked_until and user.locked_until > datetime.utcnow():
            flash("로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.", "error")
            return render_template("auth/login.html", form=form)

        if user is None or not user.check_password(form.password.data):
            if user:
                user.failed_login_count += 1
                if user.failed_login_count >= MAX_FAILS:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)
                    user.failed_login_count = 0
                db.session.commit()
            flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
            return render_template("auth/login.html", form=form)

        if not user.is_active:
            flash("비활성화된 계정입니다. 관리자에게 문의하세요.", "error")
            return render_template("auth/login.html", form=form)

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        login_user(user, remember=form.remember.data)

        next_page = request.args.get("next")
        return redirect(next_page if is_safe_next(next_page) else url_for("dashboard.index"))

    return render_template("auth/login.html", form=form)

@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("auth.login"))
```

### login.html (요약)
```html
{% extends "base.html" %}
{% block content %}
<main class="login-card">
  <h1>대시보드 로그인</h1>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, msg in messages %}
      <p class="flash flash-{{ category }}">{{ msg }}</p>
    {% endfor %}
  {% endwith %}

  <form method="post" novalidate>
    {{ form.hidden_tag() }}
    {{ form.email.label }} {{ form.email(autocomplete="username") }}
    {{ form.password.label }} {{ form.password(autocomplete="current-password") }}
    <label>{{ form.remember() }} 로그인 상태 유지</label>
    {{ form.submit(class="btn-primary") }}
  </form>
</main>
{% endblock %}
```

---

## 11. 테스트 체크리스트

- [ ] 올바른 계정으로 로그인 → `/dashboard` 이동
- [ ] 잘못된 비밀번호 → 공통 에러 메시지 표시
- [ ] 존재하지 않는 이메일 → 동일한 공통 에러 메시지
- [ ] 5회 연속 실패 → 계정 일시 잠금
- [ ] 비활성 계정 → 안내 메시지
- [ ] CSRF 토큰 없이 POST → 400
- [ ] `next=/dashboard/reports` → 해당 페이지로 이동
- [ ] `next=https://evil.com` → 무시하고 `/dashboard`로 이동
- [ ] 로그인 상태에서 `/login` 접근 → `/dashboard` 리다이렉트
- [ ] "로그인 상태 유지" 체크 → 브라우저 재시작 후에도 로그인 유지
- [ ] 로그아웃 후 `/dashboard` 접근 → `/login?next=...` 리다이렉트

---

## 12. 추후 확장

- 비밀번호 찾기 / 재설정 (이메일 토큰)
- 소셜 로그인 (Google OAuth)
- 2단계 인증 (TOTP)
- 역할 기반 권한 (관리자 / 일반 사용자)
- 로그인 이력 로그 페이지
