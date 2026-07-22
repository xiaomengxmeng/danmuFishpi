"""Fishpi authentication: login with username/password to obtain an API key."""

import hashlib
import httpx

BASE_URL = "https://fishpi.cn"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)


def md5_password(password: str) -> str:
    """Return lowercase hex MD5 of the password."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def login(username: str, password: str, mfa_code: str = "") -> dict:
    """Login to fishpi and return {'success': bool, 'api_key': str, 'error': str|None}.

    POSTs to /api/getKey with MD5-hashed password.
    """
    payload = {
        "nameOrEmail": username,
        "userPassword": md5_password(password),
    }
    if mfa_code:
        payload["mfaCode"] = mfa_code

    try:
        resp = httpx.post(
            f"{BASE_URL}/api/getKey",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"success": False, "api_key": "", "error": str(e)}

    if data.get("code") != 0:
        return {"success": False, "api_key": "",
                "error": data.get("msg", "登录失败")}

    return {"success": True, "api_key": data.get("Key", ""),
            "error": None}
