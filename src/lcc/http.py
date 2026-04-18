from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass


class HttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    data: object


def _make_headers(*, token: str, cookie: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://booking.lib.buaa.edu.cn",
        "Referer": "https://booking.lib.buaa.edu.cn/h5/index.html",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "authorization": f"bearer{token}",
        "Cookie": cookie,
        "Connection": "keep-alive",
    }

def _build_opener(*, ctx: ssl.SSLContext, use_proxy: bool) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPSHandler(context=ctx)]
    if not use_proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _is_tls_eof_error(err: BaseException) -> bool:
    s = str(err)
    if "EOF occurred in violation of protocol" in s:
        return True
    reason = getattr(err, "reason", None)
    if isinstance(reason, ssl.SSLError) and "EOF occurred in violation of protocol" in str(reason):
        return True
    return False


def post_json(
    *,
    base_url: str,
    path: str,
    token: str,
    cookie: str,
    json_body: object,
    timeout_sec: float = 15.0,
    verify_ssl: bool = True,
    use_proxy: bool | None = None,
) -> object:
    url = base_url.rstrip("/") + path
    body_bytes = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        method="POST",
        data=body_bytes,
        headers=_make_headers(token=token, cookie=cookie),
    )

    if use_proxy is None:
        v = (os.environ.get("LCC_NO_PROXY") or "").strip().lower()
        use_proxy = v not in ("1", "true", "yes", "on")

    ctx = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()  # noqa: SLF001
    opener = _build_opener(ctx=ctx, use_proxy=bool(use_proxy))
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise HttpError(f"返回不是 JSON（HTTP {resp.status}）: {raw[:200]}") from e
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        raise HttpError(f"HTTP {e.code}: {raw[:200]}") from e
    except urllib.error.URLError as e:
        # A common failure mode when a system proxy / middlebox breaks TLS:
        # retry once with proxy disabled (even if use_proxy=True).
        if bool(use_proxy) and _is_tls_eof_error(e):
            try:
                opener2 = _build_opener(ctx=ctx, use_proxy=False)
                with opener2.open(req, timeout=timeout_sec) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as je:
                        raise HttpError(f"返回不是 JSON（HTTP {resp.status}）: {raw[:200]}") from je
            except urllib.error.URLError:
                pass
        hint = ""
        if _is_tls_eof_error(e):
            hint = "（提示：这通常是代理/中间人导致 TLS 被截断；可设置 LCC_NO_PROXY=1 强制不走代理）"
        raise HttpError(f"网络错误: {e}{hint}") from e
