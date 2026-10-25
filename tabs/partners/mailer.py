"""Gmail 발송(SMTP) · 답장/반송 확인(IMAP) — 앱 비밀번호 방식 (README 3-2 · 3-4).

- 계정: envkeys GMAIL_ADDRESS · GMAIL_APP_PASSWORD (설정 › 연결 → .env 순서). 비밀번호는 응답·로그에 넣지 않음
- 발송: smtp.gmail.com:465 (SSL) · 보낸 메일마다 Message-ID를 만들어 이력에 저장 (답장 추적용)
- 답장 확인: imap.gmail.com:993 · 전체보관함(\\All)을 읽기 전용(EXAMINE)으로 열고 BODY.PEEK로 읽음 → 읽음 표시를 바꾸지 않음
  · Message-ID가 있으면 In-Reply-To / References로 정확히 매칭
  · 없으면 수신자 주소 + 'Re:' 제목으로 추정 (estimated=True → 메모에 '추정')
  · 반송: mailer-daemon/postmaster 알림의 실패 수신자 헤더(X-Failed-Recipients · Final-Recipient)
- 테스트는 connect_smtp · connect_imap을 바꿔 끼워 인터넷 없이 확인합니다.
"""
import imaplib
import mimetypes
import re
import smtplib
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from server import envkeys

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
TIMEOUT = 30
FROM_NAME = "REBOOT Partnerships"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailError(Exception):
    """화면에 그대로 보여 줄 발송·확인 실패 이유 (비밀번호·내부 정보 없음)."""


def credentials():
    address = (envkeys.get("GMAIL_ADDRESS") or "").strip()
    password = (envkeys.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()  # 앱 비밀번호는 띄어 써도 됨
    return address, password


def ready():
    address, password = credentials()
    return bool(address and password)


def connect_smtp():
    return smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=ssl.create_default_context())


def connect_imap():
    return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)


# ───────── 발송 ─────────

def build_message(sender, recipients, subject, body, attachments=(), in_reply_to="", references=""):
    """→ (EmailMessage, Message-ID). attachments: [(보이는 이름, 파일 경로)]"""
    msg = EmailMessage()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else None
    message_id = make_msgid(idstring="reboot", domain=domain)
    msg["Message-ID"] = message_id
    msg["Date"] = formatdate(usegmt=True)
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    for name, path in attachments:
        ctype, _ = mimetypes.guess_type(name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            data = fh.read()
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg, message_id


def send(msg, sender, password):
    try:
        server = connect_smtp()
    except (socket.timeout, TimeoutError):
        raise MailError("Gmail 서버 연결 시간이 지났습니다. 잠시 뒤 다시 시도하세요.")
    except (OSError, smtplib.SMTPException):
        raise MailError("Gmail 서버(smtp.gmail.com:465)에 연결하지 못했습니다. 네트워크·방화벽을 확인하세요 (Render 무료 플랜은 발송 포트가 막혀 있음).")
    try:
        server.login(sender, password)
        refused = server.send_message(msg)
        if refused:
            raise MailError("일부 수신자에게 보내지 못했습니다: " + ", ".join(refused))
    except smtplib.SMTPAuthenticationError:
        raise MailError("Gmail 로그인에 실패했습니다. 설정 › 연결에서 Gmail 주소와 앱 비밀번호(16자리)를 확인하세요.")
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError("수신자 주소를 받을 수 없습니다: " + ", ".join(e.recipients))
    except smtplib.SMTPSenderRefused:
        raise MailError("발송 계정이 거부되었습니다. Gmail 주소를 확인하세요.")
    except smtplib.SMTPDataError as e:
        raise MailError(f"Gmail이 메일을 거부했습니다 (코드 {e.smtp_code}). 첨부 용량·내용을 확인하세요.")
    except (socket.timeout, TimeoutError):
        raise MailError("발송 중 시간이 지났습니다. 이력에서 발송 여부를 확인한 뒤 다시 시도하세요.")
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"발송에 실패했습니다 ({type(e).__name__}).")
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ───────── 답장 · 반송 확인 ─────────

def check(records, sender, password, own_ids=()):
    """records: [{id, message_id, to, subject, at(UTC)}] → [{id, status('회신 받음'|'반송'), at, from, subject, body,
    message_id, first, estimated, failed}] — 찾은 것만.
    own_ids: 우리가 보낸 메일의 Message-ID 목록 (자기 자신에게 보낸 테스트에서 우리 메일을 회신으로 착각하지 않게)."""
    try:
        imap = connect_imap()
    except (OSError, imaplib.IMAP4.error):
        raise MailError("Gmail IMAP(imap.gmail.com:993)에 연결하지 못했습니다. 네트워크를 확인하세요.")
    try:
        try:
            imap.login(sender, password)
        except imaplib.IMAP4.error:
            raise MailError("Gmail IMAP 로그인에 실패했습니다. 앱 비밀번호를 확인하고 Gmail 설정에서 IMAP 사용을 켜 주세요.")
        typ, _ = imap.select(_all_mail(imap), readonly=True)
        if typ != "OK":
            raise MailError("Gmail 전체보관함을 열지 못했습니다.")
        me = sender.lower()
        found = {}
        for rec in records:
            hit = _find_reply(imap, rec, me, own_ids)
            if hit:
                found[rec["id"]] = hit
        for hit in _find_bounces(imap, records, found):
            found.setdefault(hit["id"], hit)
        return list(found.values())
    except MailError:
        raise
    except (imaplib.IMAP4.error, OSError) as e:
        raise MailError(f"Gmail 답장 확인 중 오류가 났습니다 ({type(e).__name__}). 잠시 뒤 다시 시도하세요.")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _all_mail(imap):
    """전체보관함 이름 (언어 설정에 따라 이름이 달라서 \\All 표시로 찾음)."""
    try:
        typ, data = imap.list()
    except Exception:
        typ, data = "NO", []
    if typ == "OK":
        for line in data or []:
            s = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
            if "\\All" in s.split(")")[0]:
                m = re.search(r'"([^"]+)"\s*$', s) or re.search(r"\s(\S+)\s*$", s)
                if m:
                    return '"' + m.group(1).replace('"', "") + '"'
    return '"[Gmail]/All Mail"'


def _quote(s):
    return '"' + str(s).replace("\\", "").replace('"', "") + '"'


def _imap_date(dt):
    return f"{dt.day:02d}-{MONTHS[dt.month - 1]}-{dt.year}"


def _search(imap, *criteria):
    try:
        typ, data = imap.uid("SEARCH", *criteria)
    except imaplib.IMAP4.error:
        return []
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch(imap, uid):
    try:
        typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")   # PEEK: 읽음 표시 안 함
    except imaplib.IMAP4.error:
        return None
    if typ != "OK":
        return None
    for part in data or []:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            try:
                return BytesParser(policy=policy.default).parsebytes(bytes(part[1]))
            except Exception:
                return None
    return None


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _addresses(value):
    return [a for _, a in getaddresses([str(value or "")]) if a]


def _from_addr(m):
    return parseaddr(str(m.get("From", "")))[1].lower()


def _msg_date(m):
    try:
        dt = parsedate_to_datetime(str(m.get("Date", "")))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _refers_to(m, mid):
    return mid in str(m.get("In-Reply-To", "")) or mid in str(m.get("References", ""))


def _subject_core(s):
    s = re.sub(r"^\s*((re|fw|fwd|답장|회신|전달)\s*:\s*)+", "", str(s or ""), flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def is_bounce(m):
    frm = _from_addr(m)
    if frm.split("@")[0] in ("mailer-daemon", "postmaster"):
        return True
    if m.get("X-Failed-Recipients"):
        return True
    return m.get_content_type() == "multipart/report" and "delivery-status" in str(m.get_param("report-type") or "").lower()


def failed_recipients(m):
    out = []
    for h in m.get_all("X-Failed-Recipients") or []:
        out += _addresses(h)
    if not out:
        for part in m.walk():
            if part.get_content_type() != "message/delivery-status":
                continue
            payload = part.get_payload()
            for block in payload if isinstance(payload, list) else []:
                fr = block.get("Final-Recipient") or block.get("Original-Recipient")
                if fr:
                    out.append(str(fr).split(";")[-1].strip())
    return _dedupe([a.lower() for a in out if "@" in a])


def _html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[ \t]+", " ", text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def text_of(m):
    try:
        part = m.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        raw = part.get_payload(decode=True) or b""
        text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = _html_to_text(text)
    return str(text).replace("\r\n", "\n").strip()


_QUOTE_HEAD = re.compile(r"^(on\s.{0,240}wrote:|-{2,}\s*original message\s*-{2,}|_{5,}|from:\s.+|보낸 사람:\s.+|.{0,160}님이 작성:|20\d\d년 .{0,120}작성:?)\s*$", re.I)


def strip_quoted(text):
    """인용된 원문(> …, 'On … wrote:' 아래)을 뺀 회신 본문."""
    lines = str(text or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">"):
            continue
        joined = (s + " " + lines[i + 1].strip()) if i + 1 < len(lines) else s
        if _QUOTE_HEAD.match(s) or (s.lower().startswith("on ") and _QUOTE_HEAD.match(joined)):
            break
        out.append(line)
    return "\n".join(out).strip()


def first_sentence(text):
    s = " ".join(x.strip() for x in str(text or "").split("\n") if x.strip())
    m = re.match(r"(.+?[.!?。])(\s|$)", s)
    return (m.group(1) if m else s)[:200]


def _result(rec, m, status, estimated):
    text = text_of(m)
    body = strip_quoted(text) if status == "회신 받음" else text
    return {"id": rec["id"], "status": status, "at": _msg_date(m), "from": str(m.get("From", ""))[:300],
            "subject": str(m.get("Subject", ""))[:300], "message_id": str(m.get("Message-ID", "")).strip()[:300],
            "body": body[:20000], "first": first_sentence(body), "estimated": estimated,
            "failed": failed_recipients(m) if status == "반송" else []}


def _find_reply(imap, rec, me, own_ids=()):
    mid = str(rec.get("message_id") or "").strip()
    to_me = me in {a.lower() for a in _addresses(rec.get("to"))}   # 내 Gmail → 내 Gmail 테스트
    sent_at = rec.get("at") or datetime.now(timezone.utc).replace(tzinfo=None)
    since = _imap_date(sent_at - timedelta(days=1))
    msgs, estimated = [], False
    if mid:
        q = _quote(mid)
        uids = _dedupe(_search(imap, "HEADER", "In-Reply-To", q) + _search(imap, "HEADER", "References", q))
        msgs = [m for m in (_fetch(imap, u) for u in uids[:15]) if m is not None and _refers_to(m, mid)]
        if not msgs:  # 헤더 검색이 안 될 때: 수신자에게서 온 메일 중 우리 Message-ID를 가리키는 것
            for addr in _addresses(rec.get("to"))[:3]:
                for u in _search(imap, "FROM", _quote(addr), "SINCE", since)[-15:]:
                    m = _fetch(imap, u)
                    if m is not None and _refers_to(m, mid):
                        msgs.append(m)
    elif rec.get("to") and rec.get("subject"):
        estimated = True
        core = _subject_core(rec["subject"])
        for addr in _addresses(rec.get("to"))[:3]:
            for u in _search(imap, "FROM", _quote(addr), "SINCE", since)[-30:]:
                m = _fetch(imap, u)
                subj = str(m.get("Subject", "")) if m is not None else ""
                if m is not None and re.match(r"^\s*(re|답장|회신)\s*:", subj, re.I) and _subject_core(subj) == core:
                    msgs.append(m)
    own = {mid, *own_ids} - {""}
    msgs = [m for m in msgs if (to_me or _from_addr(m) != me) and str(m.get("Message-ID", "")).strip() not in own
            and _msg_date(m) >= sent_at - timedelta(hours=1)]
    if not msgs:
        return None
    msgs.sort(key=_msg_date)
    replies = [m for m in msgs if not is_bounce(m)]
    if replies:
        return _result(rec, replies[0], "회신 받음", estimated)
    return _result(rec, msgs[0], "반송", estimated)


def _find_bounces(imap, records, done):
    pending = [r for r in records if r["id"] not in done]
    dated = [r["at"] for r in pending if r.get("at")]
    if not pending or not dated:
        return []
    since = _imap_date(min(dated) - timedelta(days=1))
    uids = _dedupe(_search(imap, "FROM", '"mailer-daemon"', "SINCE", since) + _search(imap, "FROM", '"postmaster"', "SINCE", since))
    out, taken = [], set()
    for u in uids[-50:]:
        m = _fetch(imap, u)
        if m is None or not is_bounce(m):
            continue
        try:
            blob = m.as_string()
        except Exception:
            blob = text_of(m)
        failed = set(failed_recipients(m))
        bdate = _msg_date(m)
        target = next((r for r in pending if r.get("message_id") and r["message_id"] in blob), None)
        if target is None and failed:
            cands = [r for r in pending if r.get("at") and r["at"] <= bdate + timedelta(minutes=5)
                     and {a.lower() for a in _addresses(r.get("to"))} & failed]
            target = max(cands, key=lambda r: r["at"]) if cands else None
        if target is None or target["id"] in taken:
            continue
        taken.add(target["id"])
        hit = _result(target, m, "반송", False)
        hit["failed"] = sorted(failed) or hit["failed"]
        out.append(hit)
    return out
