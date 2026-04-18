from __future__ import annotations

import datetime as _dt
import json
import subprocess


class CryptoError(RuntimeError):
    pass


IV_STR = "ZZWBKJ_ZHIHUAWEI"


def _date_to_key_hex(day: str | None = None) -> str:
    """
    Key = YYYYMMDD + reverse(YYYYMMDD), then interpreted as UTF-8 bytes, then hex.
    """
    if day:
        d = day.replace("-", "").strip()
        if len(d) != 8 or not d.isdigit():
            raise CryptoError("day 必须是 YYYY-MM-DD 或 YYYYMMDD")
        day8 = d
    else:
        day8 = _dt.date.today().strftime("%Y%m%d")
    key_str = day8 + day8[::-1]
    if len(key_str) != 16:
        raise CryptoError("内部错误：key 长度不是 16")
    return key_str.encode("utf-8").hex()


def _iv_hex() -> str:
    return IV_STR.encode("utf-8").hex()


def aesjson_encrypt(data: object, *, day: str | None = None) -> str:
    """
    Encrypt JSON.stringify(data) into base64 ciphertext string (aesjson).
    Uses system openssl to avoid external Python deps.
    """
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    key_hex = _date_to_key_hex(day)
    iv_hex = _iv_hex()

    try:
        p = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-128-cbc",
                "-K",
                key_hex,
                "-iv",
                iv_hex,
                "-nosalt",
                "-base64",
                "-A",
            ],
            input=plaintext,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise CryptoError("找不到 openssl：无法进行 AES 加密") from e
    except subprocess.CalledProcessError as e:
        raise CryptoError(f"openssl 加密失败: {(e.stderr or b'').decode('utf-8', errors='replace')[:200]}") from e

    return (p.stdout or b"").decode("utf-8", errors="replace").strip()


def aesjson_decrypt(aesjson: str, *, day: str | None = None) -> str:
    """
    Decrypt base64 ciphertext (aesjson) to plaintext string.
    """
    aesjson = (aesjson or "").strip()
    if not aesjson:
        raise CryptoError("aesjson 为空")

    key_hex = _date_to_key_hex(day)
    iv_hex = _iv_hex()
    try:
        p = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-128-cbc",
                "-K",
                key_hex,
                "-iv",
                iv_hex,
                "-nosalt",
                "-base64",
                "-A",
            ],
            input=aesjson.encode("utf-8"),
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise CryptoError("找不到 openssl：无法进行 AES 解密") from e
    except subprocess.CalledProcessError as e:
        raise CryptoError(f"openssl 解密失败（可能 day 不对）: {(e.stderr or b'').decode('utf-8', errors='replace')[:200]}") from e

    return (p.stdout or b"").decode("utf-8", errors="replace")
