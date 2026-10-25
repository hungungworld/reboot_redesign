"""로컬 실행: python -m server [--port 5000] [--host 127.0.0.1]

처음 실행 전에 관리자 계정을 만듭니다 (둘 중 하나)
  - .env에 ADMIN_EMAIL · ADMIN_PASSWORD 를 넣고 실행 (계정이 하나도 없을 때만 만들어짐)
  - flask --app server create-user you@example.com --name 홍길동 --admin
"""
import argparse
import os

from . import create_app


def main():
    ap = argparse.ArgumentParser(description="REBOOT 대시보드 로컬 서버")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    ap.add_argument("--no-debug", action="store_true", help="자동 새로고침(디버그) 끄기")
    args = ap.parse_args()
    app = create_app()
    print(f"\n  REBOOT 대시보드: http://{args.host}:{args.port}/   (끄려면 Ctrl+C)\n")
    app.run(host=args.host, port=args.port, debug=not args.no_debug, use_reloader=not args.no_debug)


if __name__ == "__main__":
    main()
