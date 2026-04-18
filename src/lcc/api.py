from __future__ import annotations

from .auth import ensure_logged_in
from .config import load_auth
from .http import HttpError, post_json


def _is_auth_error(resp: object) -> bool:
    if not isinstance(resp, dict):
        return False
    code = resp.get("code")
    return code == 10001


def post_json_authed(
    *,
    path: str,
    json_body: object,
    timeout_sec: float,
    insecure: bool,
    verify_ssl: bool,
    use_proxy: bool | None = None,
) -> object:
    """
    POST JSON with auto-login + one retry on auth error.
    """
    ensure_logged_in(insecure=insecure or (not verify_ssl), timeout_sec=timeout_sec, use_proxy=use_proxy)
    auth = load_auth()
    try:
        resp = post_json(
            base_url=auth.base_url,
            path=path,
            token=auth.token,
            cookie=auth.cookie,
            json_body=json_body,
            timeout_sec=timeout_sec,
            verify_ssl=verify_ssl,
            use_proxy=use_proxy,
        )
    except HttpError as e:
        msg = str(e)
        if "HTTP 401" in msg or "HTTP 403" in msg:
            ensure_logged_in(
                insecure=insecure or (not verify_ssl), timeout_sec=timeout_sec, force=True, use_proxy=use_proxy
            )
            auth = load_auth()
            return post_json(
                base_url=auth.base_url,
                path=path,
                token=auth.token,
                cookie=auth.cookie,
                json_body=json_body,
                timeout_sec=timeout_sec,
                verify_ssl=verify_ssl,
                use_proxy=use_proxy,
            )
        raise

    if _is_auth_error(resp):
        ensure_logged_in(insecure=insecure or (not verify_ssl), timeout_sec=timeout_sec, force=True, use_proxy=use_proxy)
        auth = load_auth()
        return post_json(
            base_url=auth.base_url,
            path=path,
            token=auth.token,
            cookie=auth.cookie,
            json_body=json_body,
            timeout_sec=timeout_sec,
            verify_ssl=verify_ssl,
            use_proxy=use_proxy,
        )
    return resp
