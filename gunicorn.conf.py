"""gunicorn 설정 (Render).

- 워커 1개 + 스레드 8개: SQLite와 프로세스 메모리 잠금(로그인 제한·작업 잠금)을 단순하게 유지
- timeout 300초: AI 판정·규제 모니터링 수집처럼 오래 걸리는 요청 허용
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 300
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
# 요청 기록에 쿼리 문자열(검색어 등)은 남기지 않음
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)ss'
