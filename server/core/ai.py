"""OpenAI 호출 공통 도우미 (공통 담당).

홈·인사이트·콘텐츠·파트너·성과분석은 README대로 OpenAI를 씁니다.
(컴플라이언스·CS는 tabs/compliance · tabs/cs의 Claude 모듈을 그대로 씁니다.)

    from server.core.ai import openai_json, AIError, openai_ready
    data = openai_json(system, user, schema=SCHEMA, part="home", action="뉴스 요약")

- 키: envkeys.get("OPENAI_API_KEY")  (설정 › 연결 화면 입력 → .env 순서)
- 모델: envkeys.get("OPENAI_MODEL") (기본 gpt-4o-mini) — 호출마다 model= 로 바꿀 수 있음
- schema를 주면 JSON Schema(strict)로, 없으면 JSON 객체 모드로 받음
- 성공한 호출은 작업 로그(AI 프롬프트)에 모델·토큰·프롬프트 요약을 남김 (키·이메일은 가림)
- 실패는 AIError(화면에 그대로 보여 줄 한국어 문장, HTTP 상태)
"""
import json

from .. import envkeys, worklog


class AIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def openai_ready():
    return envkeys.has("OPENAI_API_KEY")


def openai_model():
    return envkeys.get("OPENAI_MODEL") or "gpt-4o-mini"


def _client(timeout, max_retries=1):
    import openai
    if not openai_ready():
        raise AIError("OpenAI 키가 없습니다. 설정 › 연결에서 OPENAI_API_KEY를 넣어 주세요.", 503)
    return openai.OpenAI(api_key=envkeys.get("OPENAI_API_KEY"), timeout=timeout, max_retries=max_retries)


def _call(messages, response_format, model, max_tokens, timeout, max_retries=1):
    import openai
    client = _client(timeout, max_retries)
    try:
        kwargs = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
        if response_format:
            kwargs["response_format"] = response_format
        return client.chat.completions.create(**kwargs)
    except openai.AuthenticationError:
        raise AIError("OpenAI 키가 올바르지 않습니다 (설정 › 연결 확인).", 503)
    except (openai.PermissionDeniedError, openai.NotFoundError):
        raise AIError(f"이 키로는 '{model}' 모델을 쓸 수 없습니다 (설정 › 연결의 OpenAI 모델 확인).", 503)
    except openai.RateLimitError:
        raise AIError("요청이 많거나 사용 한도를 넘었습니다. 잠시 뒤 다시 시도하세요.", 429)
    except openai.BadRequestError as e:
        raise AIError(f"AI 요청을 처리하지 못했습니다 ({getattr(e, 'code', None) or 'bad_request'}).", 400)
    except openai.APITimeoutError:
        raise AIError("AI 응답이 늦어 중단했습니다. 다시 시도하세요.", 504)
    except openai.APIConnectionError:
        raise AIError("AI 서버에 연결하지 못했습니다 (인터넷 확인).", 502)
    except openai.APIStatusError as e:
        raise AIError(f"AI 서버 오류 ({e.status_code}). 잠시 뒤 다시 시도하세요.", 502)


def _usage(res):
    u = getattr(res, "usage", None)
    if not u:
        return {}
    return {"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None),
            "total": getattr(u, "total_tokens", None)}


def _log(part, action, model, system, user, summary, res):
    if not part:
        return
    worklog.add(part, "AI 프롬프트", action or "AI 호출", detail={
        "model": getattr(res, "model", model), "tokens": _usage(res),
        "prompt": (f"[system]\n{system}\n\n[user]\n{user}")[:6000], "response": str(summary)[:800]})


def openai_json(system, user, *, schema=None, schema_name="result", model=None, max_tokens=4000, timeout=90,
                part="", action="", max_retries=1):
    """JSON 객체(dict)를 돌려줌. user가 dict/list면 JSON 문자열로 바꿔 보냄.
    max_retries: SDK 자동 재시도 횟수 (오래 걸리는 호출은 0으로 두면 최악 대기 시간이 절반)."""
    model = model or openai_model()
    if not isinstance(user, str):
        user = json.dumps(user, ensure_ascii=False)
    rf = ({"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
          if schema else {"type": "json_object"})
    sys_text = system if schema else system + "\n\nRespond with a single JSON object only."
    res = _call([{"role": "system", "content": sys_text}, {"role": "user", "content": user}], rf, model, max_tokens,
                timeout, max_retries)
    choice = res.choices[0]
    if choice.finish_reason == "length":
        raise AIError("AI 응답이 길어 잘렸습니다. 입력을 줄여 다시 시도하세요.")
    if getattr(choice.message, "refusal", None):
        raise AIError("AI가 이 요청을 거절했습니다. 내용을 바꿔 다시 시도하세요.", 422)
    text = choice.message.content or ""
    try:
        data = json.loads(text)
    except ValueError:
        raise AIError("AI 응답을 읽지 못했습니다. 다시 시도하세요.")
    if not isinstance(data, dict):
        raise AIError("AI 응답 형식이 올바르지 않습니다. 다시 시도하세요.")
    _log(part, action, model, system, user, text, res)
    return data


def openai_text(system, user, *, model=None, max_tokens=2000, timeout=90, part="", action="", max_retries=1):
    """일반 문장(문자열)을 돌려줌."""
    model = model or openai_model()
    res = _call([{"role": "system", "content": system}, {"role": "user", "content": user}], None, model, max_tokens,
                timeout, max_retries)
    choice = res.choices[0]
    if getattr(choice.message, "refusal", None):
        raise AIError("AI가 이 요청을 거절했습니다. 내용을 바꿔 다시 시도하세요.", 422)
    text = (choice.message.content or "").strip()
    if not text:
        raise AIError("AI가 빈 응답을 돌려줬습니다. 다시 시도하세요.")
    _log(part, action, model, system, user, text, res)
    return text
