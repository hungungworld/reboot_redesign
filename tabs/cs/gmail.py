"""CS Gmail 연동: 문의 읽기 + 답장 초안 만들기 + 승인된 답장 바로 보내기.

보내기는 2026-10-24 담당(소준희) 결정으로 추가했습니다. 화면 '보내기' 탭에서 최종 승인된 답장만, 받는 사람을 확인한 뒤
보냅니다. 서버도 [확인 필요]가 남았거나 승인 정보가 없거나 BLOCK이면 거절합니다 (routes.py). 권한은 기존 gmail.compose
(초안 관리·보내기)라 다시 로그인하지 않아도 됩니다.

Flask 통합 (원본: JUNHEE/server/cs/gmail.py — 읽기·초안 로직은 같음)
- 로그인 정보 위치: DATA_DIR/google/token.json (Render 영구 디스크). 깃에 올리지 않습니다.
  파일 대신 환경변수 GMAIL_TOKEN_JSON(token.json 내용)으로도 줄 수 있습니다.
- 로그인 방법
  1) 웹: 설정 › 연결 › Google로 로그인  (GOOGLE_CLIENT_ID · GOOGLE_CLIENT_SECRET, 웹 애플리케이션 클라이언트)
  2) 로컬: flask --app server gmail-login  (DATA_DIR/google/credentials.json = 데스크톱 앱 클라이언트 JSON)
- OAuth 동의 화면이 '테스트'면 7일마다 다시 로그인합니다.
"""
import base64
import html
import json
import os
import re
from email.message import EmailMessage
from pathlib import Path

from flask import current_app

from server.envkeys import get as env

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


class LoginRequired(Exception):
    """token.json이 없거나 만료됨 → 다시 로그인."""


def google_dir():
    d = Path(current_app.config["DATA_DIR"]) / "google"
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path():
    return google_dir() / "token.json"


def _off_path():
    # '연결 해제' 표시: 환경변수 GMAIL_TOKEN_JSON으로 받은 로그인도 쓰지 않음 (GPT 검토 ⑳) · 다시 로그인하면 지워짐
    return google_dir() / "disconnected"


def credentials_path():
    return google_dir() / "credentials.json"


def ready():
    """로그인 정보가 있는지만 빠르게 확인 (만료 여부는 실제 호출 때 확인)."""
    if _off_path().exists():
        return False
    return token_path().exists() or bool(os.environ.get("GMAIL_TOKEN_JSON", "").strip())


def save_credentials(creds):
    tmp = token_path().with_suffix(".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    tmp.replace(token_path())
    _off_path().unlink(missing_ok=True)   # 다시 로그인했으면 연결 해제 표시를 지움


def disconnect():
    token_path().unlink(missing_ok=True)
    _off_path().write_text("1", encoding="utf-8")


def _load_credentials():
    from google.oauth2.credentials import Credentials
    if _off_path().exists():
        return None
    if token_path().exists():
        return Credentials.from_authorized_user_file(str(token_path()), SCOPES)
    raw = os.environ.get("GMAIL_TOKEN_JSON", "").strip()
    if raw:
        return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    return None


def service(interactive=False):
    # interactive=False (웹 요청): 브라우저 로그인을 띄우지 않고 LoginRequired를 냄
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = _load_credentials()
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds)
        except RefreshError:
            creds = None  # 테스트 모드 7일 만료 → 다시 로그인
    if not creds or not creds.valid:
        if not interactive:
            raise LoginRequired("Gmail 로그인이 필요합니다. 설정 › 연결에서 Google로 로그인하거나, "
                                "로컬에서 flask --app server gmail-login 을 실행하세요.")
        from google_auth_oauthlib.flow import InstalledAppFlow
        if not credentials_path().exists():
            raise LoginRequired(f"{credentials_path()} 가 없습니다. Google Cloud에서 데스크톱 앱 클라이언트 JSON을 받아 "
                                "이 이름으로 저장하세요.")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path()), SCOPES)
        creds = flow.run_local_server(port=0)
        save_credentials(creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _headers(msg):
    return {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}


def _find_part(payload, mime):
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = _find_part(part, mime)
        if text:
            return text
    return ""


def html_to_text(raw):
    """HTML 메일 → 읽을 수 있는 글 (스크립트·스타일 제거, 줄바꿈 유지, &amp; 등 풀기)."""
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def _body_text(payload):
    # text/plain 본문을 먼저 찾고, 없으면 HTML 본문을 글로 바꿈 (HTML만 있는 메일이 빈칸이 되던 문제 · GPT 검토 ⑱)
    return _find_part(payload, "text/plain") or html_to_text(_find_part(payload, "text/html"))


DEFAULT_QUERY = "in:inbox -from:me newer_than:30d"   # 받은편지함 · 내가 보낸 메일 제외 (GPT 검토 ⑲) · CS 전용 주소가 있으면 GMAIL_QUERY로 좁힘


def list_inquiries(svc, query=None, limit=20):
    """GMAIL_QUERY에 맞는 문의를 [{id, thread_id, from, subject, date, received_ms, body}]로 돌려줌 (다음 페이지까지 limit건)."""
    query = query if query is not None else (env("GMAIL_QUERY", DEFAULT_QUERY) or DEFAULT_QUERY)
    refs, token = [], None
    while len(refs) < limit:
        page = svc.users().messages().list(userId="me", q=query, maxResults=min(100, limit - len(refs)),
                                           **({"pageToken": token} if token else {})).execute()
        refs += page.get("messages", [])
        token = page.get("nextPageToken")
        if not token:
            break
    out = []
    for ref in refs[:limit]:
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        h = _headers(msg)
        out.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": h.get("from", ""),
            "subject": h.get("subject", ""),
            "date": h.get("date", ""),
            "received_ms": int(msg.get("internalDate", 0)),
            "body": _body_text(msg["payload"]),
        })
    return out


def _reply(svc, message_id, body):
    """원래 문의와 같은 스레드로 가는 답장 (받는 사람 · Re: 제목 · In-Reply-To) → (Gmail API용 본문, 받는 사람, 제목)."""
    orig = svc.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
    ).execute()
    h = _headers(orig)
    subject = h.get("subject", "")
    reply = EmailMessage()
    reply["To"] = h.get("reply-to") or h.get("from", "")
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if h.get("message-id"):
        reply["In-Reply-To"] = h["message-id"]
        reply["References"] = f'{h.get("references", "")} {h["message-id"]}'.strip()
    reply.set_content(body)
    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    return {"raw": raw, "threadId": orig["threadId"]}, reply["To"], reply["Subject"]


def reply_preview(svc, message_id):
    """보내기 전에 화면에 보여 줄 받는 사람·제목 (보내지 않음)."""
    _, to, subject = _reply(svc, message_id, "")
    return {"to": to, "subject": subject}


def create_reply_draft(svc, message_id, body):
    """원래 문의와 같은 스레드에 답장 '초안'을 만듦. 발송하지 않음."""
    msg, _, _ = _reply(svc, message_id, body)
    draft = svc.users().drafts().create(userId="me", body={"message": msg}).execute()
    return draft["id"]


def send_internal(svc, to, subject, body):
    """팀 담당자에게 새 메일 (문의 전달 · 승인 요청 · 부작용 접수 공유). 받는 사람 확인은 routes.py가 연락처로."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return svc.users().messages().send(userId="me", body={"raw": raw}).execute()["id"]


def send_reply(svc, message_id, body):
    """원래 문의와 같은 스레드로 답장을 바로 보냄 → (보낸 메일 id, 받는 사람)."""
    msg, to, _ = _reply(svc, message_id, body)
    sent = svc.users().messages().send(userId="me", body=msg).execute()
    return sent["id"], to


def profile_email(svc):
    return svc.users().getProfile(userId="me").execute().get("emailAddress", "")
