"""Tests for auth.login() MFA detection."""
import auth
from unittest.mock import patch, MagicMock


def test_login_success_no_mfa():
    """Successful login should return need_mfa=False."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"code": 0, "Key": "test-api-key"}
    with patch("auth.httpx.post", return_value=mock_resp):
        result = auth.login("user", "pass")
    assert result["success"] is True
    assert result["api_key"] == "test-api-key"
    assert result["need_mfa"] is False


def test_login_mfa_required():
    """When server returns '两步验证失败' msg, need_mfa should be True."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "code": -1,
        "msg": "两步验证失败，请输入验证码"
    }
    with patch("auth.httpx.post", return_value=mock_resp):
        result = auth.login("user", "pass")
    assert result["success"] is False
    assert result["need_mfa"] is True
    assert "两步验证失败" in result["error"]


def test_login_wrong_password_no_mfa():
    """Normal login failure (wrong password) should not trigger need_mfa."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "code": -1,
        "msg": "密码错误"
    }
    with patch("auth.httpx.post", return_value=mock_resp):
        result = auth.login("user", "wrongpass")
    assert result["success"] is False
    assert result["need_mfa"] is False


def test_login_with_mfa_code():
    """Login with mfa_code should include it in payload."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"code": 0, "Key": "key-with-mfa"}
    with patch("auth.httpx.post", return_value=mock_resp) as mock_post:
        result = auth.login("user", "pass", mfa_code="123456")
    # Verify mfaCode was in the request payload
    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload["mfaCode"] == "123456"
    assert result["success"] is True
    assert result["need_mfa"] is False
