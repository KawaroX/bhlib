from __future__ import annotations

import datetime as _dt

from .api import post_json_authed
from .config import ConfigError, cache_area_tree, get_cached_area_tree


def _today_iso() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def fetch_area_tree(
    *,
    day: str | None = None,
    timeout_sec: float = 15.0,
    insecure: bool = False,
    verify_ssl: bool = True,
    use_proxy: bool | None = None,
) -> dict:
    """
    Fetch full premise/storey/area tree via pcTopFor + pick per premise.

    Returns:
        {
          "day": "YYYY-MM-DD",
          "premises": [
            {
              "id": "9", "name": "学院路校区图书馆",
              "total_num": int, "free_num": int,
              "storeys": [
                {
                  "id": "10", "name": "一楼",
                  "total_num": int, "free_num": int,
                  "areas": [
                    {"id": "8", "name": "一层西阅学空间",
                     "nameMerge": "学院路校区图书馆/一楼/一层西阅学空间",
                     "parentStoreyId": "10", "premiseId": "9",
                     "typeName": "普通座位", "typeCategory": "1",
                     "total_num": int, "free_num": int},
                    ...
                  ],
                },
                ...
              ],
            },
            ...
          ],
        }
    """
    day = day or _today_iso()
    top = post_json_authed(
        path="/v4/space/pcTopFor",
        json_body={"day": day},
        timeout_sec=timeout_sec,
        insecure=insecure,
        verify_ssl=verify_ssl,
        use_proxy=use_proxy,
    )
    if not isinstance(top, dict) or top.get("code") != 0:
        raise ConfigError(f"pcTopFor 返回异常: {top!r}")

    raw_premises = (top.get("data") or {}).get("list") or []
    premises_out: list[dict] = []

    for pr in raw_premises:
        pr_id = str(pr.get("id") or "").strip()
        if not pr_id:
            continue

        # storeys from pcTopFor (areas are empty here, need pick)
        storey_by_id: dict[str, dict] = {}
        for st in pr.get("children") or []:
            st_id = str(st.get("id") or "").strip()
            if not st_id:
                continue
            storey_by_id[st_id] = {
                "id": st_id,
                "name": str(st.get("name") or ""),
                "total_num": _as_int(st.get("total_num")),
                "free_num": _as_int(st.get("free_num")),
                "areas": [],
            }

        # fetch areas for this premise
        pick = post_json_authed(
            path="/v4/space/pick",
            json_body={"id": pr_id, "day": day},
            timeout_sec=timeout_sec,
            insecure=insecure,
            verify_ssl=verify_ssl,
            use_proxy=use_proxy,
        )
        if not isinstance(pick, dict) or pick.get("code") != 0:
            raise ConfigError(f"pick(id={pr_id}) 返回异常: {pick!r}")

        pick_data = pick.get("data") or {}
        for a in pick_data.get("area") or []:
            a_id = str(a.get("id") or "").strip()
            parent_id = str(a.get("parentId") or "").strip()
            if not a_id or parent_id not in storey_by_id:
                continue
            storey_by_id[parent_id]["areas"].append({
                "id": a_id,
                "name": str(a.get("name") or ""),
                "nameMerge": str(a.get("nameMerge") or ""),
                "parentStoreyId": parent_id,
                "premiseId": pr_id,
                "typeName": str(a.get("typeName") or ""),
                "typeCategory": str(a.get("typeCategory") or ""),
                "total_num": _as_int(a.get("total_num")),
                "free_num": _as_int(a.get("free_num")),
            })

        premises_out.append({
            "id": pr_id,
            "name": str(pr.get("name") or ""),
            "total_num": _as_int(pr.get("total_num")),
            "free_num": _as_int(pr.get("free_num")),
            "storeys": list(storey_by_id.values()),
        })

    return {"day": day, "premises": premises_out}


def _as_int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def get_or_fetch_tree(
    *,
    refresh: bool = False,
    day: str | None = None,
    timeout_sec: float = 15.0,
    insecure: bool = False,
    verify_ssl: bool = True,
    use_proxy: bool | None = None,
    max_age_sec: int = 86400,
) -> dict:
    """
    Return cached tree if fresh (and not refresh), else fetch and cache.
    """
    if not refresh:
        cached = get_cached_area_tree(max_age_sec=max_age_sec)
        if cached is not None:
            return cached
    tree = fetch_area_tree(
        day=day,
        timeout_sec=timeout_sec,
        insecure=insecure,
        verify_ssl=verify_ssl,
        use_proxy=use_proxy,
    )
    cache_area_tree(tree)
    return tree


def flatten_areas(tree: dict) -> list[dict]:
    """
    Flatten tree to leaf-area list, each enriched with premiseName/storeyName.
    """
    out: list[dict] = []
    for pr in tree.get("premises") or []:
        for st in pr.get("storeys") or []:
            for a in st.get("areas") or []:
                out.append({
                    **a,
                    "premiseName": pr.get("name", ""),
                    "storeyName": st.get("name", ""),
                })
    return out


def resolve_area_id(
    arg: str,
    *,
    timeout_sec: float = 15.0,
    insecure: bool = False,
    verify_ssl: bool = True,
    use_proxy: bool | None = None,
) -> str:
    """
    Resolve area identifier. Rules:
      - Empty/None → ConfigError.
      - Pure digits → return as-is (no network).
      - Otherwise → fuzzy substring match against area name / nameMerge
        (case-insensitive). Returns the matched id; raises ConfigError with
        candidates on multi-match or zero-match.
    """
    s = (arg or "").strip()
    if not s:
        raise ConfigError("area 参数为空")
    if s.isdigit():
        return s

    tree = get_or_fetch_tree(
        timeout_sec=timeout_sec,
        insecure=insecure,
        verify_ssl=verify_ssl,
        use_proxy=use_proxy,
    )
    areas = flatten_areas(tree)
    needle = s.casefold()

    # exact name match first, then nameMerge/name substring
    exact = [a for a in areas if a["name"].casefold() == needle]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        raise ConfigError(_format_candidates(f"名字 '{s}' 精确匹配到多个区域，请用 id 或更完整的名字：", exact))

    matches = [a for a in areas if needle in a["name"].casefold() or needle in a["nameMerge"].casefold()]
    if not matches:
        raise ConfigError(f"找不到匹配 '{s}' 的区域。用 `bhlib areas` 查看可用区域")
    if len(matches) == 1:
        return matches[0]["id"]
    raise ConfigError(_format_candidates(f"'{s}' 匹配到多个区域，请缩小范围（或直接传 id）：", matches))


def _format_candidates(prefix: str, areas: list[dict]) -> str:
    lines = [prefix]
    for a in areas[:12]:
        lines.append(f"  {a['id']:>4}  {a['nameMerge']}  (free {a['free_num']}/{a['total_num']})")
    if len(areas) > 12:
        lines.append(f"  …还有 {len(areas) - 12} 个")
    return "\n".join(lines)
