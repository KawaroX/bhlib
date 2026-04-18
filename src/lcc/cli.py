from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from getpass import getpass

from .api import post_json_authed
from .areas import flatten_areas, get_or_fetch_tree, resolve_area_id
from .config import (
    ConfigError,
    clear_auth,
    cache_segment,
    get_cached_segment,
    load_auth,
    load_auth_loose,
    save_auth,
    update_defaults,
)
from .cas import CasLoginError, cas_login
from .crypto import CryptoError, aesjson_decrypt, aesjson_encrypt
from .http import HttpError
from .env import load_env


def _effective_verify_ssl(auth, args: argparse.Namespace) -> bool:
    if getattr(args, "insecure", False):
        return False
    return bool(getattr(auth, "verify_ssl", True))

def _effective_use_proxy(args: argparse.Namespace) -> bool:
    if getattr(args, "no_proxy", False):
        return False
    v = (os.environ.get("LCC_NO_PROXY") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return False
    return True

def _fetch_subscribe(args: argparse.Namespace, auth, *, timeout: float, verify_ssl: bool, insecure: bool) -> object:
    return post_json_authed(
        path="/v4/index/subscribe",
        json_body={},
        timeout_sec=timeout,
        insecure=bool(insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )


def _pick_my_light_device(subscribe_resp: object, *, prefer_area_id: str | None = None) -> dict:
    if not isinstance(subscribe_resp, dict):
        raise ConfigError("subscribe 返回结构异常：不是对象")
    data = subscribe_resp.get("data")
    if not isinstance(data, list):
        raise ConfigError("subscribe 返回结构异常：data 不是数组")

    prefer_area_id = str(prefer_area_id).strip() if prefer_area_id is not None else None

    def _has_light(it: dict) -> bool:
        v = it.get("hasLight")
        return v in (1, "1", True)

    candidates: list[dict] = [it for it in data if isinstance(it, dict) and _has_light(it)]
    if prefer_area_id:
        preferred = [it for it in candidates if str(it.get("area_id") or "") == prefer_area_id]
        if preferred:
            candidates = preferred

    if not candidates:
        raise ConfigError("未找到可用的灯光设备（subscribe 里没有 hasLight=1 的条目）")

    picked = candidates[0]
    if not picked.get("id") or not picked.get("area_id"):
        raise ConfigError("subscribe 条目缺少 id/area_id")
    return picked


def _pick_my_active_item(subscribe_resp: object, *, prefer_area_id: str | None = None) -> dict:
    if not isinstance(subscribe_resp, dict):
        raise ConfigError("subscribe 返回结构异常：不是对象")
    data = subscribe_resp.get("data")
    if not isinstance(data, list) or not data:
        raise ConfigError("subscribe 里没有数据（可能当前没有座位/预约）")

    prefer_area_id = str(prefer_area_id).strip() if prefer_area_id is not None else None
    items = [it for it in data if isinstance(it, dict)]
    if prefer_area_id:
        preferred = [it for it in items if str(it.get("area_id") or "") == prefer_area_id]
        if preferred:
            items = preferred
    return items[0]


def _space_payload_from_subscribe_item(item: dict, *, style: str) -> dict:
    device_id = str(item.get("id") or "").strip()
    seat_id = str(item.get("space_id") or item.get("space") or "").strip()
    area_id = str(item.get("area_id") or "").strip()

    if style == "device_points":
        if not device_id:
            raise ConfigError("subscribe 条目里没有找到 id（smartDevice id）")
        return {"id": device_id, "points": {}}
    if style == "id":
        if not seat_id:
            raise ConfigError("subscribe 条目里没有找到 space_id/space")
        if not area_id:
            raise ConfigError("subscribe 条目里没有找到 area_id")
        return {"id": seat_id, "area_id": area_id}
    if style == "space_id":
        if not seat_id:
            raise ConfigError("subscribe 条目里没有找到 space_id/space")
        if not area_id:
            raise ConfigError("subscribe 条目里没有找到 area_id")
        return {"space_id": seat_id, "area_id": area_id}
    raise ConfigError(f"未知 style: {style}")


def _fetch_seat_resp(
    args: argparse.Namespace,
    *,
    area_id: str,
    day: str,
    start_time: str,
    end_time: str,
    timeout: float,
    insecure: bool,
    verify_ssl: bool,
) -> dict:
    resp = post_json_authed(
        path="/v4/Space/seat",
        json_body={
            "id": str(area_id),
            "day": day,
            "label_id": [],
            "start_time": start_time,
            "end_time": end_time,
            "begdate": "",
            "enddate": "",
        },
        timeout_sec=timeout,
        insecure=insecure,
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    if not isinstance(resp, dict):
        raise ConfigError("seat 接口返回结构异常：不是对象")
    return resp


def _extract_segment_from_seat_resp(resp: dict) -> str | None:
    d = resp.get("data")
    if isinstance(d, dict):
        for k in ("segment", "segment_id", "segmentId"):
            v = d.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def _discover_segment_in_obj(obj: object, *, start_time: str, end_time: str) -> str | None:
    """
    Best-effort segment discovery from an arbitrary JSON object.
    Looks for dicts that contain a segment id, optionally matching start/end time.
    """

    def _iter_dicts(x: object):
        stack = [x]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                yield cur
                for v in cur.values():
                    stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)

    start_time = (start_time or "").strip()
    end_time = (end_time or "").strip()
    candidates: list[tuple[str, str | None, str | None]] = []
    for d in _iter_dicts(obj):
        seg = d.get("segment") or d.get("segment_id") or d.get("segmentId")
        if seg is None:
            continue
        seg_s = str(seg).strip()
        if not seg_s:
            continue
        st = d.get("start_time") or d.get("startTime") or d.get("beginTime") or d.get("begin_time")
        et = d.get("end_time") or d.get("endTime")
        st_s = str(st).strip() if st is not None else None
        et_s = str(et).strip() if et is not None else None
        candidates.append((seg_s, st_s, et_s))

    if not candidates:
        return None

    # Prefer exact time match when possible.
    for seg_s, st_s, et_s in candidates:
        if st_s and et_s and st_s == start_time and et_s == end_time:
            return seg_s

    # If there is only one unique segment in the object, use it.
    uniq = sorted({c[0] for c in candidates})
    if len(uniq) == 1:
        return uniq[0]

    return None


def _fetch_segment_from_map(
    args: argparse.Namespace,
    *,
    area_id: str,
    day: str,
    start_time: str,
    end_time: str,
    verify_ssl: bool,
) -> str | None:
    """
    Fetch segment from /v4/Space/map.
    Response: data.date.list[*].times[*].{id, start, end}
    where times[i].id IS the segment value for that time slot.
    """
    try:
        resp = post_json_authed(
            path="/v4/Space/map",
            json_body={"id": area_id},
            timeout_sec=float(getattr(args, "timeout", 15)),
            insecure=bool(getattr(args, "insecure", False)),
            verify_ssl=verify_ssl,
            use_proxy=_effective_use_proxy(args),
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(resp, dict):
        return None
    data = resp.get("data")
    if not isinstance(data, dict):
        return None
    date_obj = data.get("date")
    if not isinstance(date_obj, dict):
        return None
    date_list = date_obj.get("list")
    if not isinstance(date_list, list):
        return None

    # Prefer the entry for the requested day; fall back to all entries.
    day_entries = [e for e in date_list if isinstance(e, dict) and str(e.get("day", "")).startswith(day)]
    if not day_entries:
        day_entries = [e for e in date_list if isinstance(e, dict)]

    start_time = (start_time or "").strip()
    end_time = (end_time or "").strip()
    candidates: list[tuple[str, str, str]] = []  # (seg_id, t_start, t_end)
    for entry in day_entries:
        times = entry.get("times")
        if not isinstance(times, list):
            continue
        for t in times:
            if not isinstance(t, dict):
                continue
            seg_id = str(t.get("id") or "").strip()
            if not seg_id:
                continue
            t_start = str(t.get("start") or "").strip()
            t_end = str(t.get("end") or "").strip()
            candidates.append((seg_id, t_start, t_end))

    if not candidates:
        return None
    # Exact match first.
    for seg_id, t_start, t_end in candidates:
        if t_start == start_time and t_end == end_time:
            return seg_id
    # Time slot that contains the requested range.
    for seg_id, t_start, t_end in candidates:
        if t_start and t_end and t_start <= start_time and t_end >= end_time:
            return seg_id
    # Single candidate — use it.
    if len(candidates) == 1:
        return candidates[0][0]
    return None


def _extract_segment_from_list_resp(resp: object, *, start_time: str, end_time: str) -> str | None:
    """
    Extract segment from a segment-list style response where each item's own
    'id' (or similar) field IS the segment value, paired with start/end time fields.

    Example response shape:
      {"data": [{"id": "2285237", "start_time": "19:00", "end_time": "23:00"}, ...]}
    or
      {"data": {"list": [{"id": "2285237", "startTime": "19:00", "endTime": "23:00"}, ...]}}
    """
    def _iter_items(x: object):
        if isinstance(x, dict):
            data = x.get("data")
            if isinstance(data, list):
                yield from (i for i in data if isinstance(i, dict))
                return
            if isinstance(data, dict):
                for key in ("list", "rows", "items", "segments", "times"):
                    lst = data.get(key)
                    if isinstance(lst, list):
                        yield from (i for i in lst if isinstance(i, dict))
                        return
                # data itself might be the only item
                yield from _iter_items(data)
        elif isinstance(x, list):
            yield from (i for i in x if isinstance(i, dict))

    start_time = (start_time or "").strip()
    end_time = (end_time or "").strip()
    candidates: list[tuple[str, str | None, str | None]] = []

    for item in _iter_items(resp):
        # The segment value is the item's own id.
        seg = item.get("id") or item.get("segmentId") or item.get("segment_id")
        if seg is None:
            continue
        seg_s = str(seg).strip()
        if not seg_s:
            continue
        st = (item.get("start_time") or item.get("startTime")
              or item.get("beginTime") or item.get("begin_time"))
        et = item.get("end_time") or item.get("endTime")
        st_s = str(st).strip() if st is not None else None
        et_s = str(et).strip() if et is not None else None
        candidates.append((seg_s, st_s, et_s))

    if not candidates:
        return None

    # Prefer exact time-range match.
    for seg_s, st_s, et_s in candidates:
        if st_s and et_s and st_s == start_time and et_s == end_time:
            return seg_s

    # If only one candidate, use it.
    if len(candidates) == 1:
        return candidates[0][0]

    return None


def _fetch_segment_from_api(
    args: argparse.Namespace,
    *,
    area_id: str,
    day: str,
    start_time: str,
    end_time: str,
    verify_ssl: bool,
) -> str | None:
    """
    Try known segment-list endpoints to auto-discover the segment ID for
    (area_id, day, start_time, end_time).  Tries both segment-list format
    (each item's id IS the segment) and embedded-segment format.
    """
    candidate_paths = [
        "/v4/Space/segment",
        "/v4/space/segment",
        "/v4/area/segment",
        "/v4/Space/time",
        "/v4/area/time",
        "/v4/Space/opendays",
    ]
    payloads = [
        {"id": area_id, "day": day},
        {"area_id": area_id, "day": day},
        {"id": area_id},
        {"area_id": area_id},
    ]
    for path in candidate_paths:
        for payload in payloads:
            try:
                resp = post_json_authed(
                    path=path,
                    json_body=payload,
                    timeout_sec=float(getattr(args, "timeout", 15)),
                    insecure=bool(getattr(args, "insecure", False)),
                    verify_ssl=verify_ssl,
                    use_proxy=_effective_use_proxy(args),
                )
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(resp, dict):
                continue
            # Try both extraction strategies.
            seg = _extract_segment_from_list_resp(
                resp, start_time=start_time, end_time=end_time
            ) or _discover_segment_in_obj(resp, start_time=start_time, end_time=end_time)
            if seg:
                return seg
    return None


def _extract_seats_from_seat_resp(resp: dict) -> list[dict]:
    d = resp.get("data")
    if not isinstance(d, dict):
        return []
    lst = d.get("list")
    if not isinstance(lst, list):
        return []
    return [it for it in lst if isinstance(it, dict)]


def _cmd_auth_set(args: argparse.Namespace) -> int:
    # Preserve defaults if file exists
    default_area_id = None
    try:
        default_area_id = load_auth().default_area_id
    except ConfigError:
        default_area_id = None
    save_auth(
        token=args.token,
        cookie=args.cookie,
        base_url=args.base_url,
        verify_ssl=(not args.insecure),
        default_area_id=default_area_id,
    )
    print("OK: 已写入 .lcc.json（当前目录）")
    return 0


def _redact(value: str, keep: int = 6) -> str:
    value = value or ""
    if len(value) <= keep:
        return "*" * len(value)
    return ("*" * (len(value) - keep)) + value[-keep:]


def _cmd_auth_show(args: argparse.Namespace) -> int:
    auth = load_auth()
    print(json.dumps(
        {
            "base_url": auth.base_url,
            "token": _redact(auth.token),
            "cookie": _redact(auth.cookie),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def _cmd_auth_clear(args: argparse.Namespace) -> int:
    clear_auth()
    print("OK: 已删除 .lcc.json")
    return 0


def _cmd_auth_login(args: argparse.Namespace) -> int:
    env = load_env()
    username = args.username or (env.get("LCC_USERNAME") or "").strip()
    if not username:
        raise ConfigError("缺少 username：请传 --username 或在 .env 里设置 LCC_USERNAME")

    password = args.password or (env.get("LCC_PASSWORD") or "")
    if not password:
        if args.no_prompt:
            raise ConfigError("缺少密码：请传 --password 或在 .env 里设置 LCC_PASSWORD")
        password = getpass("Password: ")
    try:
        result = cas_login(
            username=username,
            password=password,
            initial_booking_cookie=args.seed_cookie,
            timeout_sec=args.timeout,
            verify_ssl=(not args.insecure),
        )
    except CasLoginError as e:
        raise ConfigError(str(e)) from e

    default_area_id = None
    try:
        default_area_id = load_auth().default_area_id
    except ConfigError:
        default_area_id = None
    save_auth(
        token=result.token,
        cookie=result.cookie,
        base_url=args.base_url,
        verify_ssl=(not args.insecure),
        default_area_id=default_area_id,
    )
    print("OK: 登录成功，已写入 .lcc.json（token + booking 域 cookie）")
    return 0


def _cmd_light_set(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    sub = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))

    device_id_arg = str(args.device_id).strip() if getattr(args, "device_id", None) is not None else None
    area_id_arg = str(args.area_id).strip() if getattr(args, "area_id", None) is not None else None

    if device_id_arg:
        picked = None
        if isinstance(sub, dict) and isinstance(sub.get("data"), list):
            for it in sub["data"]:
                if not isinstance(it, dict):
                    continue
                if str(it.get("id") or "").strip() != device_id_arg:
                    continue
                if it.get("hasLight") not in (1, "1", True):
                    continue
                if area_id_arg and str(it.get("area_id") or "").strip() != area_id_arg:
                    continue
                picked = it
                break
        if not picked:
            raise ConfigError(
                "指定的 --device-id/--area-id 不在当前账号的 subscribe(hasLight=1) 列表里；"
                "出于安全考虑，不支持控制非本人座位的灯。"
            )
    else:
        picked = _pick_my_light_device(sub, prefer_area_id=args.prefer_area_id)

    device_id = str(picked["id"])
    area_id = str(picked["area_id"])

    payload = {
        "id": device_id,
        "area_id": area_id,
        "brightness": int(args.brightness),
    }
    data = post_json_authed(
        path="/reserve/smartDevice/setLightBrightness",
        json_body=payload,
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_light_list(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    if args.data:
        try:
            payload = json.loads(args.data)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--data 不是合法 JSON: {e}") from e
    elif args.area_id:
        resolved = _resolve_area_id_maybe(args.area_id, args, auth=auth)
        payload = {"area_id": str(resolved)}
    else:
        payload = {}

    data = post_json_authed(
        path=args.path,
        json_body=payload,
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _resolve_my_light_device_ids(
    args: argparse.Namespace,
    *,
    timeout: float,
) -> tuple[str, str, bool]:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    sub = _fetch_subscribe(args, auth, timeout=timeout, verify_ssl=verify_ssl, insecure=bool(args.insecure))
    picked = _pick_my_light_device(sub, prefer_area_id=getattr(args, "prefer_area_id", None))
    return str(picked["id"]), str(picked["area_id"]), verify_ssl


def _set_light_brightness_by_ids(
    *,
    device_id: str,
    area_id: str,
    brightness: int,
    timeout: float,
    insecure: bool,
    verify_ssl: bool,
    use_proxy: bool,
) -> object:
    payload = {"id": str(device_id), "area_id": str(area_id), "brightness": int(brightness)}
    return post_json_authed(
        path="/reserve/smartDevice/setLightBrightness",
        json_body=payload,
        timeout_sec=float(timeout),
        insecure=bool(insecure),
        verify_ssl=bool(verify_ssl),
        use_proxy=bool(use_proxy),
    )


def _flash_light_brightness(
    args: argparse.Namespace,
    *,
    low: int,
    high: int,
    cycles: int,
    interval: float,
) -> None:
    if cycles <= 0:
        raise ConfigError("--cycles 必须是正整数")
    if interval < 0:
        raise ConfigError("--interval 不能为负数")

    timeout = float(getattr(args, "timeout", 15.0))
    device_id, area_id, verify_ssl = _resolve_my_light_device_ids(args, timeout=timeout)

    # Pattern: low -> (high -> low) * cycles  (so high is reached `cycles` times)
    _set_light_brightness_by_ids(
        device_id=device_id,
        area_id=area_id,
        brightness=int(low),
        timeout=timeout,
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    if interval > 0:
        time.sleep(float(interval))
    for _ in range(int(cycles)):
        _set_light_brightness_by_ids(
            device_id=device_id,
            area_id=area_id,
            brightness=int(high),
            timeout=timeout,
            insecure=bool(args.insecure),
            verify_ssl=verify_ssl,
            use_proxy=_effective_use_proxy(args),
        )
        if interval > 0:
            time.sleep(float(interval))
        _set_light_brightness_by_ids(
            device_id=device_id,
            area_id=area_id,
            brightness=int(low),
            timeout=timeout,
            insecure=bool(args.insecure),
            verify_ssl=verify_ssl,
            use_proxy=_effective_use_proxy(args),
        )
        if interval > 0:
            time.sleep(float(interval))


def _cmd_pomo_flash(args: argparse.Namespace) -> int:
    _flash_light_brightness(
        args,
        low=int(args.low),
        high=int(args.high),
        cycles=int(args.cycles),
        interval=float(args.interval),
    )
    print(f"OK: 已闪烁 {args.cycles} 次（{args.low}->{args.high}->{args.low}）")
    return 0


def _cmd_pomo_start(args: argparse.Namespace) -> int:
    if args.seconds is not None:
        total_sec = float(args.seconds)
    else:
        total_sec = float(args.minutes) * 60.0
    if total_sec <= 0:
        raise ConfigError("番茄钟时长必须为正数（--minutes/--seconds）")

    end_at = _dt.datetime.now() + _dt.timedelta(seconds=total_sec)
    print(f"Pomodoro 开始：{total_sec:.0f}s，预计结束时间：{end_at.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        time.sleep(total_sec)
    except KeyboardInterrupt:
        print("已取消（Ctrl-C）")
        return 130

    print("时间到：开始闪烁灯光…")
    _flash_light_brightness(
        args,
        low=int(args.low),
        high=int(args.high),
        cycles=int(args.cycles),
        interval=float(args.interval),
    )
    print("OK: 番茄钟完成")
    return 0


def _cmd_me_subscribe(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    data = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_me_current(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    data = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))

    try:
        item = _pick_my_active_item(data, prefer_area_id=args.prefer_area_id)
    except ConfigError:
        print(json.dumps({"active": False}, ensure_ascii=False, indent=2))
        return 0
    seat_no = item.get("no") or item.get("spaceName") or ""
    area = item.get("areaName") or item.get("nameMerge") or ""
    status_name = item.get("statusname") or item.get("status_name") or ""
    brightness = item.get("brightness")
    device_id = item.get("id")
    area_id = item.get("area_id")

    out = {
        "area_id": area_id,
        "seat_no": seat_no,
        "status": status_name,
        "brightness": brightness,
        "device_id": device_id,
        "area": area,
        "beginTime": item.get("beginTime"),
        "endTime": item.get("endTime"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_crypto_encrypt(args: argparse.Namespace) -> int:
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        raise ConfigError(f"--data 不是合法 JSON: {e}") from e
    try:
        s = aesjson_encrypt(data, day=args.day)
    except CryptoError as e:
        raise ConfigError(str(e)) from e
    print(s)
    return 0


def _cmd_crypto_decrypt(args: argparse.Namespace) -> int:
    try:
        s = aesjson_decrypt(args.aesjson, day=args.day)
    except CryptoError as e:
        raise ConfigError(str(e)) from e

    s_strip = s.strip()
    if args.json:
        try:
            obj = json.loads(s_strip)
        except json.JSONDecodeError:
            obj = {"plaintext": s_strip}
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(s_strip)
    return 0


def _cmd_space_leave(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    if args.data:
        try:
            payload = json.loads(args.data)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--data 不是合法 JSON: {e}") from e
    else:
        sub = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))
        item = _pick_my_active_item(sub, prefer_area_id=args.prefer_area_id)
        payload = _space_payload_from_subscribe_item(item, style=args.style)

    aesjson = aesjson_encrypt(payload, day=args.day)
    if args.dry_run:
        print(json.dumps({"payload": payload, "aesjson": aesjson}, ensure_ascii=False, indent=2))
        return 0

    data = post_json_authed(
        path="/v4/space/leave",
        json_body={"aesjson": aesjson},
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_space_signin(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    if args.data:
        try:
            payload = json.loads(args.data)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--data 不是合法 JSON: {e}") from e
    else:
        sub = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))
        item = _pick_my_active_item(sub, prefer_area_id=args.prefer_area_id)
        payload = _space_payload_from_subscribe_item(item, style=args.style)

    aesjson = aesjson_encrypt(payload, day=args.day)
    if args.dry_run:
        print(json.dumps({"payload": payload, "aesjson": aesjson}, ensure_ascii=False, indent=2))
        return 0

    data = post_json_authed(
        path="/v4/space/signin",
        json_body={"aesjson": aesjson},
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_space_action(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)

    path = str(args.path or "").strip()
    if not path:
        raise ConfigError("缺少 --path（例如 /v4/space/leave）")

    if args.data:
        try:
            payload = json.loads(args.data)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--data 不是合法 JSON: {e}") from e
    else:
        sub = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))
        item = _pick_my_active_item(sub, prefer_area_id=args.prefer_area_id)
        payload = _space_payload_from_subscribe_item(item, style=args.style)

    aesjson = aesjson_encrypt(payload, day=args.day)
    if args.dry_run:
        print(json.dumps({"path": path, "payload": payload, "aesjson": aesjson}, ensure_ascii=False, indent=2))
        return 0

    data = post_json_authed(
        path=path,
        json_body={"aesjson": aesjson},
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_space_finish(args: argparse.Namespace) -> int:
    args.path = "/v4/space/checkout"
    return _cmd_space_action(args)


def _cmd_space_book(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    env = load_env()

    day = args.day or _dt.date.today().isoformat()
    end_time = args.end_time or "23:00"
    start_time = args.start_time or _dt.datetime.now().strftime("%H:%M")

    area_id = _resolve_area_id_maybe(args.area_id, args, auth=auth) or auth.default_area_id
    if not area_id:
        raise ConfigError("缺少 area_id：请传 --area-id 或设置默认 LCC_DEFAULT_AREA_ID")

    if start_time >= end_time:
        raise ConfigError(f"时间区间无效：start_time={start_time} end_time={end_time}")

    # Fetch seat list (for both display and segment discovery).
    seat_resp = _fetch_seat_resp(
        args,
        area_id=str(area_id),
        day=day,
        start_time=start_time,
        end_time=end_time,
        timeout=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
    )
    segment = (str(args.segment).strip() if args.segment else None) \
        or _extract_segment_from_seat_resp(seat_resp) \
        or _discover_segment_in_obj(seat_resp, start_time=start_time, end_time=end_time)
    if not segment:
        # Try another seat call with empty time range: some deployments return segment lists that way.
        try:
            seat_resp2 = _fetch_seat_resp(
                args,
                area_id=str(area_id),
                day=day,
                start_time="",
                end_time="",
                timeout=float(args.timeout),
                insecure=bool(args.insecure),
                verify_ssl=verify_ssl,
            )
        except Exception:  # noqa: BLE001
            seat_resp2 = {}
        segment = _extract_segment_from_seat_resp(seat_resp2) or _discover_segment_in_obj(
            seat_resp2, start_time=start_time, end_time=end_time
        )
    if not segment:
        segment = _fetch_segment_from_map(
            args,
            area_id=str(area_id),
            day=day,
            start_time=start_time,
            end_time=end_time,
            verify_ssl=verify_ssl,
        )
    if not segment:
        # Try the dedicated segment endpoint that some deployments expose.
        segment = _fetch_segment_from_api(
            args,
            area_id=str(area_id),
            day=day,
            start_time=start_time,
            end_time=end_time,
            verify_ssl=verify_ssl,
        )
    if not segment:
        # Last resort: check subscribe response (active booking for same area may carry segment).
        try:
            sub_resp = post_json_authed(
                path="/v4/index/subscribe",
                json_body={},
                timeout_sec=float(args.timeout),
                insecure=bool(args.insecure),
                verify_ssl=verify_ssl,
                use_proxy=_effective_use_proxy(args),
            )
            segment = _extract_segment_from_list_resp(
                sub_resp, start_time=start_time, end_time=end_time
            ) or _discover_segment_in_obj(sub_resp, start_time=start_time, end_time=end_time)
        except Exception:  # noqa: BLE001
            pass
    if not segment:
        segment = (env.get("LCC_DEFAULT_SEGMENT") or "").strip() or None
    if not segment:
        segment = get_cached_segment(area_id=str(area_id), start_time=start_time, end_time=end_time)
    seats = _extract_seats_from_seat_resp(seat_resp)
    if not seats:
        raise ConfigError("seat 接口没有返回座位列表")

    if not getattr(args, "all", False):
        seats_show = [s for s in seats if str(s.get("status") or "") == "1"]
    else:
        seats_show = seats

    def _s(v) -> str:
        return "" if v is None else str(v)

    # Determine seat_id.
    seat_id: str | None = None
    if args.seat_id:
        seat_id = str(args.seat_id).strip()
    elif args.seat_no:
        seat_no = str(args.seat_no).strip().lstrip("0")
        matches = [s for s in seats if _s(s.get("no")).lstrip("0") == seat_no]
        if not matches:
            raise ConfigError(f"找不到 seat_no={args.seat_no}")
        seat_id = _s(matches[0].get("id"))
    else:
        header = f"area_id={area_id} day={day} {start_time}-{end_time}"
        if segment:
            header += f" segment={segment}"
        print(header)
        print(f"{'id':>7}  {'no':>4}  {'status':>6}  status_name")
        for s in seats_show[:300]:
            print(f"{_s(s.get('id')):>7}  {_s(s.get('no')):>4}  {_s(s.get('status')):>6}  {_s(s.get('status_name'))}")
        if len(seats_show) > 300:
            print(f"... 仅显示前 300 条（总计 {len(seats_show)}）")

        raw = input("选择座位（默认按 seat no；支持 'id:131' / 'no:003'；直接回车取消）：").strip()
        if not raw:
            print("取消")
            return 0

        def _match_by_id(value: str) -> list[dict]:
            return [s for s in seats if _s(s.get("id")) == value]

        def _match_by_no(value: str) -> list[dict]:
            vv = value.lstrip("0")
            return [s for s in seats if _s(s.get("no")).lstrip("0") == vv]

        # Explicit prefixes.
        low = raw.lower()
        if low.startswith(("id:", "id=")):
            value = raw.split(":", 1)[1] if ":" in raw else raw.split("=", 1)[1]
            matches = _match_by_id(value.strip())
            if not matches:
                raise ConfigError(f"找不到 seat id：{value.strip()}")
            seat_id = _s(matches[0].get("id"))
        elif low.startswith(("no:", "no=")):
            value = raw.split(":", 1)[1] if ":" in raw else raw.split("=", 1)[1]
            matches = _match_by_no(value.strip())
            if not matches:
                raise ConfigError(f"找不到 seat no：{value.strip()}")
            seat_id = _s(matches[0].get("id"))
        else:
            # Default: treat raw as seat no (most user-friendly).
            no_matches = _match_by_no(raw)
            if no_matches:
                seat_id = _s(no_matches[0].get("id"))
            else:
                id_matches = _match_by_id(raw)
                if id_matches:
                    seat_id = _s(id_matches[0].get("id"))
                else:
                    raise ConfigError(f"找不到座位：{raw}")

    if not segment:
        # Print the first seat item's keys so we can identify the correct field name.
        _seats_debug = _extract_seats_from_seat_resp(seat_resp)
        sample = _seats_debug[0] if _seats_debug else seat_resp.get("data")
        print("--- seat 响应样本（用于定位 segment 字段）---", file=sys.stderr)
        print(json.dumps(sample, ensure_ascii=False, indent=2), file=sys.stderr)
        print("---", file=sys.stderr)
        raise ConfigError(
            "缺少 segment：seat 响应里没找到（样本已打印到 stderr）。\n"
            "请把 stderr 的输出贴到 issue，或用 `--segment <值>` 临时传入。"
        )
    cache_segment(area_id=str(area_id), start_time=start_time, end_time=end_time, segment=str(segment))

    picked_seat = next((s for s in seats if _s(s.get("id")) == str(seat_id)), None)
    if not picked_seat:
        raise ConfigError("内部错误：找不到所选座位")
    if str(picked_seat.get("status") or "") != "1":
        raise ConfigError(f"所选座位不是空闲状态：status={_s(picked_seat.get('status'))} { _s(picked_seat.get('status_name')) }")

    payload = {
        "seat_id": str(seat_id),
        "segment": str(segment),
        "day": day,
        "start_time": "",
        "end_time": "",
    }
    aesjson = aesjson_encrypt(payload, day=args.crypto_day)
    if args.dry_run:
        print(json.dumps({"payload": payload, "aesjson": aesjson}, ensure_ascii=False, indent=2))
        return 0

    data = post_json_authed(
        path="/v4/space/confirm",
        json_body={"aesjson": aesjson},
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_seat_list(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    day = args.day or _dt.date.today().isoformat()
    end_time = args.end_time or "23:00"
    start_time = args.start_time or _dt.datetime.now().strftime("%H:%M")

    area_id = _resolve_area_id_maybe(args.area_id, args, auth=auth)
    if not area_id:
        area_id = auth.default_area_id
    if not area_id and args.area_from_subscribe:
        sub = _fetch_subscribe(args, auth, timeout=float(args.timeout), verify_ssl=verify_ssl, insecure=bool(args.insecure))
        item = _pick_my_active_item(sub, prefer_area_id=args.prefer_area_id)
        area_id = str(item.get("area_id") or "").strip() or None
    if not area_id:
        raise ConfigError("缺少 area_id：请传 --area-id 或在 .env/.lcc.json 设置默认 LCC_DEFAULT_AREA_ID（或用 --area-from-subscribe）")

    if (args.start_time is None) and (args.end_time is None) and start_time >= end_time:
        raise ConfigError(f"默认时间区间无效：start_time={start_time} end_time={end_time}（请手动指定 --start-time/--end-time 或 --day）")

    payload = {
        "id": str(area_id),
        "day": day,
        "label_id": list(args.label_id or []),
        "start_time": start_time,
        "end_time": end_time,
        "begdate": "",
        "enddate": "",
    }

    data = post_json_authed(
        path="/v4/Space/seat",
        json_body=payload,
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    items = (((data or {}).get("data") or {}).get("list") or []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise ConfigError("接口返回结构异常：data.list 不是数组")

    segment = None
    if isinstance(data, dict):
        d = data.get("data")
        if isinstance(d, dict):
            segment = d.get("segment") or d.get("segment_id") or d.get("segmentId")

    include_status = set(args.status or [])
    exclude_status = set(args.not_status or [])
    status_name_contains = (args.status_name_contains or "").strip()

    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status") or "")
        status_name = str(it.get("status_name") or "")
        if include_status and status not in include_status:
            continue
        if exclude_status and status in exclude_status:
            continue
        if status_name_contains and status_name_contains not in status_name:
            continue
        rows.append(it)

    def _s(v) -> str:
        return "" if v is None else str(v)

    seg_part = f" segment={segment}" if segment else ""
    print(f"area_id={area_id} day={day} {start_time}-{end_time}{seg_part} seats={len(rows)}")
    print(f"{'id':>7}  {'no':>4}  {'status':>6}  status_name")
    for it in rows:
        print(f"{_s(it.get('id')):>7}  {_s(it.get('no')):>4}  {_s(it.get('status')):>6}  {_s(it.get('status_name'))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lcc")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="管理 token/cookie（写入当前目录 .lcc.json）")
    sub_auth = p_auth.add_subparsers(dest="auth_cmd", required=True)

    p_auth_set = sub_auth.add_parser("set", help="写入认证信息")
    p_auth_set.add_argument("--token", required=True, help="JWT token（不含 bearer 前缀）")
    p_auth_set.add_argument("--cookie", required=True, help="原样粘贴 cookie 字符串（例如 'PHPSESSID=...; _zte_cid_=...'）")
    p_auth_set.add_argument("--base-url", default=None, help="接口 base url（默认 https://booking.lib.buaa.edu.cn）")
    p_auth_set.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_auth_set.set_defaults(func=_cmd_auth_set)

    p_auth_show = sub_auth.add_parser("show", help="查看当前认证信息（脱敏）")
    p_auth_show.set_defaults(func=_cmd_auth_show)

    p_auth_clear = sub_auth.add_parser("clear", help="删除 .lcc.json")
    p_auth_clear.set_defaults(func=_cmd_auth_clear)

    p_auth_login = sub_auth.add_parser("login", help="使用北航 SSO(CAS) 自动登录并获取 token（优先从 .env 读账号密码）")
    p_auth_login.add_argument("--username", help="学号/工号（SSO 账号），不传则读 .env 的 LCC_USERNAME")
    p_auth_login.add_argument("--password", help="SSO 密码（不传则交互式输入）")
    p_auth_login.add_argument("--seed-cookie", help="可选：用已有 booking cookie 预填（例如包含 _zte_cid_）")
    p_auth_login.add_argument("--base-url", default=None, help="接口 base url（默认 https://booking.lib.buaa.edu.cn）")
    p_auth_login.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    p_auth_login.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_auth_login.add_argument("--no-prompt", action="store_true", help="不进行交互式输入（缺少密码时直接报错）")
    p_auth_login.set_defaults(func=_cmd_auth_login)

    p_light = sub.add_parser("light", help="灯光相关")
    sub_light = p_light.add_subparsers(dest="light_cmd", required=True)

    p_light_set = sub_light.add_parser("set", help="设置亮度")
    p_light_set.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_light_set.add_argument("--device-id", help="可选：指定 smartDevice id（必须出现在当前账号的 subscribe(hasLight=1) 里）")
    p_light_set.add_argument("--area-id", help="可选：配合 --device-id 进一步限定 area_id（同样必须匹配 subscribe）")
    p_light_set.add_argument("--brightness", required=True, type=int, help="亮度值（例如 19）")
    p_light_set.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_light_set.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_light_set.set_defaults(func=_cmd_light_set)

    p_light_list = sub_light.add_parser("list", help="列出灯光/设备（需要你提供抓包到的列表接口 path）")
    p_light_list.add_argument("--path", required=True, help="接口路径（例如 /reserve/smartDevice/xxx）")
    p_light_list.add_argument("--area-id", help="可选：仅传 area_id（等价于 --data '{\"area_id\":\"...\"}'）")
    p_light_list.add_argument("--data", help="可选：POST JSON 字符串（例如 '{\"area_id\":\"8\"}'）")
    p_light_list.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_light_list.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_light_list.set_defaults(func=_cmd_light_list)

    p_pomo = sub.add_parser("pomo", help="番茄钟（时间到后闪烁灯光）")
    sub_pomo = p_pomo.add_subparsers(dest="pomo_cmd", required=True)

    p_pomo_start = sub_pomo.add_parser("start", help="开始番茄钟")
    p_pomo_start.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_pomo_start.add_argument("--minutes", type=float, default=25.0, help="时长（分钟，默认 25）")
    p_pomo_start.add_argument("--seconds", type=float, help="时长（秒，优先级高于 --minutes，用于测试）")
    p_pomo_start.add_argument("--low", type=int, default=20, help="结束时闪烁的低亮度（默认 20）")
    p_pomo_start.add_argument("--high", type=int, default=40, help="结束时闪烁的高亮度（默认 40）")
    p_pomo_start.add_argument("--cycles", type=int, default=2, help="到达高亮度的次数（默认 2）")
    p_pomo_start.add_argument("--interval", type=float, default=0.0, help="每次亮度变化间隔秒数（默认 0，建议网慢时保持 0）")
    p_pomo_start.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_pomo_start.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_pomo_start.set_defaults(func=_cmd_pomo_start)

    p_pomo_flash = sub_pomo.add_parser("flash", help="立即执行一次闪烁（便于测试）")
    p_pomo_flash.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_pomo_flash.add_argument("--low", type=int, default=20, help="低亮度（默认 20）")
    p_pomo_flash.add_argument("--high", type=int, default=40, help="高亮度（默认 40）")
    p_pomo_flash.add_argument("--cycles", type=int, default=2, help="到达高亮度的次数（默认 2）")
    p_pomo_flash.add_argument("--interval", type=float, default=0.0, help="每次亮度变化间隔秒数（默认 0，建议网慢时保持 0）")
    p_pomo_flash.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_pomo_flash.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_pomo_flash.set_defaults(func=_cmd_pomo_flash)

    p_me = sub.add_parser("me", help="我的状态")
    sub_me = p_me.add_subparsers(dest="me_cmd", required=True)

    p_me_sub = sub_me.add_parser("subscribe", help="查询当前账号状态/预约信息（/v4/index/subscribe）")
    p_me_sub.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_me_sub.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_me_sub.set_defaults(func=_cmd_me_subscribe)

    p_me_current = sub_me.add_parser("current", help="用 subscribe 输出一份精简摘要（座位/亮度）")
    p_me_current.add_argument("--prefer-area-id", help="如果 subscribe 有多条记录，优先选这个 area_id")
    p_me_current.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_me_current.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_me_current.set_defaults(func=_cmd_me_current)

    p_area = sub.add_parser("area", help="列出校区/楼层/区域编号（支持按名字查）")
    sub_area = p_area.add_subparsers(dest="area_cmd", required=True)

    p_area_list = sub_area.add_parser("list", help="列出全部区域（默认树形，结果缓存 24h）")
    p_area_list.add_argument("--day", help="查询日期 YYYY-MM-DD（默认今天；影响 free/total 计数）")
    p_area_list.add_argument("--json", action="store_true", help="输出原始 JSON")
    p_area_list.add_argument("--flat", action="store_true", help="扁平输出（id  完整路径  free/total）")
    p_area_list.add_argument("--refresh", action="store_true", help="跳过缓存，强制重新拉取")
    p_area_list.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_area_list.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_area_list.set_defaults(func=_cmd_area_list)

    p_prefs = sub.add_parser("prefs", help="偏好设置（写入 .lcc.json）")
    sub_prefs = p_prefs.add_subparsers(dest="prefs_cmd", required=True)

    p_prefs_set = sub_prefs.add_parser("set", help="设置默认值")
    p_prefs_set.add_argument("--default-area-id", help="常用区域 id（seat list 默认使用）")
    p_prefs_set.set_defaults(func=_prefs_set)

    p_crypto = sub.add_parser("crypto", help="aesjson 加/解密工具（用于调试）")
    sub_crypto = p_crypto.add_subparsers(dest="crypto_cmd", required=True)

    p_crypto_enc = sub_crypto.add_parser("encrypt", help="把明文 JSON 加密为 aesjson")
    p_crypto_enc.add_argument("--day", help="可选：YYYY-MM-DD 或 YYYYMMDD（默认今天）")
    p_crypto_enc.add_argument("--data", required=True, help="明文 JSON 字符串（例如 '{\"id\":\"220\"}'）")
    p_crypto_enc.set_defaults(func=_cmd_crypto_encrypt)

    p_crypto_dec = sub_crypto.add_parser("decrypt", help="把 aesjson 解密回明文")
    p_crypto_dec.add_argument("--day", help="可选：YYYY-MM-DD 或 YYYYMMDD（默认今天）")
    p_crypto_dec.add_argument("--aesjson", required=True, help="密文字符串（base64）")
    p_crypto_dec.add_argument("--json", action="store_true", help="尝试按 JSON 解析输出")
    p_crypto_dec.set_defaults(func=_cmd_crypto_decrypt)

    p_space = sub.add_parser("space", help="座位操作（写操作，可能需要 aesjson）")
    sub_space = p_space.add_subparsers(dest="space_cmd", required=True)

    p_space_leave = sub_space.add_parser("leave", help="临时离开（/v4/space/leave，需要 aesjson）")
    p_space_leave.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_space_leave.add_argument(
        "--style",
        choices=["device_points", "id", "space_id"],
        default="device_points",
        help="payload 结构（默认 device_points：和抓包一致）",
    )
    p_space_leave.add_argument("--day", help="可选：用于加密的日期 YYYY-MM-DD（默认今天）")
    p_space_leave.add_argument("--data", help="手动指定明文 JSON（会被加密成 aesjson）")
    p_space_leave.add_argument("--dry-run", action="store_true", help="只输出 payload+a​​esjson，不真正请求")
    p_space_leave.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_space_leave.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_space_leave.set_defaults(func=_cmd_space_leave)

    p_space_signin = sub_space.add_parser("signin", help="签到（/v4/space/signin，需要 aesjson）")
    p_space_signin.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_space_signin.add_argument(
        "--style",
        choices=["device_points", "id", "space_id"],
        default="device_points",
        help="payload 结构（默认 device_points：和抓包一致）",
    )
    p_space_signin.add_argument("--day", help="可选：用于加密的日期 YYYY-MM-DD（默认今天）")
    p_space_signin.add_argument("--data", help="手动指定明文 JSON（会被加密成 aesjson）")
    p_space_signin.add_argument("--dry-run", action="store_true", help="只输出 payload+a​​esjson，不真正请求")
    p_space_signin.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_space_signin.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_space_signin.set_defaults(func=_cmd_space_signin)

    p_space_action = sub_space.add_parser("action", help="发送任意 space 写接口（需要 aesjson）")
    p_space_action.add_argument("--path", required=True, help="接口路径（例如 /v4/space/leave）")
    p_space_action.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_space_action.add_argument(
        "--style",
        choices=["device_points", "id", "space_id"],
        default="device_points",
        help="payload 结构（默认 device_points：和抓包一致）",
    )
    p_space_action.add_argument("--day", help="可选：用于加密的日期 YYYY-MM-DD（默认今天）")
    p_space_action.add_argument("--data", help="手动指定明文 JSON（会被加密成 aesjson）")
    p_space_action.add_argument("--dry-run", action="store_true", help="只输出 payload+a​​esjson，不真正请求")
    p_space_action.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_space_action.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_space_action.set_defaults(func=_cmd_space_action)

    p_space_finish = sub_space.add_parser("finish", help="完全离开（/v4/space/checkout）")
    p_space_finish.add_argument("--prefer-area-id", help="subscribe 有多条记录时优先选该 area_id")
    p_space_finish.add_argument(
        "--style",
        choices=["device_points", "id", "space_id"],
        default="device_points",
        help="payload 结构（默认 device_points：和抓包一致）",
    )
    p_space_finish.add_argument("--day", help="可选：用于加密的日期 YYYY-MM-DD（默认今天）")
    p_space_finish.add_argument("--data", help="手动指定明文 JSON（会被加密成 aesjson）")
    p_space_finish.add_argument("--dry-run", action="store_true", help="只输出 payload+a​​esjson，不真正请求")
    p_space_finish.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_space_finish.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_space_finish.set_defaults(func=_cmd_space_finish)

    p_space_book = sub_space.add_parser("book", help="预约座位（/v4/space/confirm，需要 aesjson）")
    p_space_book.add_argument("--area-id", help="区域 id（不传则用默认 LCC_DEFAULT_AREA_ID / .lcc.json）")
    p_space_book.add_argument("--day", help="日期 YYYY-MM-DD（默认今天）")
    p_space_book.add_argument("--start-time", help="开始时间 HH:MM（默认当前时间）")
    p_space_book.add_argument("--end-time", help="结束时间 HH:MM（默认 23:00）")
    p_space_book.add_argument("--segment", help="可选：segment（不传则尝试从 seat 接口响应中获取）")
    p_space_book.add_argument("--seat-id", help="可选：直接指定 seat_id（不交互）")
    p_space_book.add_argument("--seat-no", help="可选：直接指定 seat no（不交互）")
    p_space_book.add_argument("--all", action="store_true", help="展示所有座位（默认只展示空闲 status=1）")
    p_space_book.add_argument("--crypto-day", help="可选：用于 aesjson 加密的日期（默认今天）")
    p_space_book.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_space_book.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_space_book.add_argument("--dry-run", action="store_true", help="只输出明文 payload+a​​esjson，不真正预约")
    p_space_book.set_defaults(func=_cmd_space_book)

    p_seat = sub.add_parser("seat", help="座位相关（查询）")
    sub_seat = p_seat.add_subparsers(dest="seat_cmd", required=True)

    p_seat_list = sub_seat.add_parser("list", help="查询座位列表（/v4/Space/seat）")
    p_seat_list.add_argument("--area-id", help="区域 id（不传则用默认 LCC_DEFAULT_AREA_ID / .lcc.json）")
    p_seat_list.add_argument("--prefer-area-id", help="配合 --area-from-subscribe：subscribe 多条记录时优先选该 area_id")
    p_seat_list.add_argument("--day", help="日期 YYYY-MM-DD（默认今天）")
    p_seat_list.add_argument("--start-time", help="开始时间 HH:MM（默认当前时间）")
    p_seat_list.add_argument("--end-time", help="结束时间 HH:MM（默认 23:00）")
    p_seat_list.add_argument("--area-from-subscribe", action="store_true", help="当没传 --area-id 且没有默认区域时，尝试用 subscribe 获取 area_id")
    p_seat_list.add_argument("--label-id", action="append", default=[], help="可选：重复传入 label_id（例如 --label-id 1 --label-id 2）")
    p_seat_list.add_argument("--status", action="append", default=[], help="仅包含这些 status（可重复）")
    p_seat_list.add_argument("--not-status", action="append", default=[], help="排除这些 status（可重复）")
    p_seat_list.add_argument("--status-name-contains", help="按 status_name 关键字过滤（例如 空闲）")
    p_seat_list.add_argument("--json", action="store_true", help="输出原始 JSON")
    p_seat_list.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    p_seat_list.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（不推荐）")
    p_seat_list.set_defaults(func=_cmd_seat_list)

    return parser


def _resolve_area_id_maybe(arg: str | None, args: argparse.Namespace, *, auth=None) -> str | None:
    """
    Resolve --area-id argument: numeric → as-is (no network), else fuzzy-match
    via area tree. Pass-through for None/empty.
    """
    if arg is None:
        return None
    s = str(arg).strip()
    if not s:
        return None
    if s.isdigit():
        return s
    auth = auth if auth is not None else load_auth_loose()
    return resolve_area_id(
        s,
        timeout_sec=float(getattr(args, "timeout", 15.0)),
        insecure=bool(getattr(args, "insecure", False)),
        verify_ssl=_effective_verify_ssl(auth, args),
        use_proxy=_effective_use_proxy(args),
    )


def _cmd_area_list(args: argparse.Namespace) -> int:
    auth = load_auth_loose()
    verify_ssl = _effective_verify_ssl(auth, args)
    tree = get_or_fetch_tree(
        refresh=bool(args.refresh),
        day=args.day,
        timeout_sec=float(args.timeout),
        insecure=bool(args.insecure),
        verify_ssl=verify_ssl,
        use_proxy=_effective_use_proxy(args),
    )

    if args.json:
        print(json.dumps(tree, ensure_ascii=False, indent=2))
        return 0

    if args.flat:
        for a in flatten_areas(tree):
            print(f"{a['id']:>4}  {a['nameMerge']}  free={a['free_num']}/{a['total_num']}  [{a['typeName']}]")
        return 0

    print(f"day={tree['day']}")
    for pr in tree["premises"]:
        print(f"◆ {pr['name']}  id={pr['id']}  free {pr['free_num']}/{pr['total_num']}")
        for st in pr["storeys"]:
            print(f"  ├ {st['name']}  id={st['id']}  free {st['free_num']}/{st['total_num']}")
            for a in st["areas"]:
                print(f"  │    {a['id']:>4}  {a['name']}  free={a['free_num']}/{a['total_num']}  [{a['typeName']}]")
    return 0


def _prefs_set(args: argparse.Namespace) -> int:
    if args.default_area_id is None:
        raise ConfigError("请至少传一个字段（例如 --default-area-id 8）")
    resolved = _resolve_area_id_maybe(args.default_area_id, args)
    update_defaults(default_area_id=resolved)
    msg = f"OK: 已更新 .lcc.json (default_area_id={resolved})"
    if resolved != args.default_area_id:
        msg += f"  ← 解析自 '{args.default_area_id}'"
    print(msg)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    no_proxy_anywhere = "--no-proxy" in raw_argv
    if no_proxy_anywhere:
        raw_argv = [a for a in raw_argv if a != "--no-proxy"]
        os.environ["LCC_NO_PROXY"] = "1"

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if no_proxy_anywhere:
        setattr(args, "no_proxy", True)
    try:
        return int(args.func(args))
    except (ConfigError, HttpError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
