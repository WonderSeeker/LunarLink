# -*- coding: utf-8 -*-
"""认证模块用例：注册边界 / 登录 / 连续失败锁定 / token 矩阵 / 登出与续签。

★ 锁定边界是最容易出 off-by-one 的地方，本文件逐条断言：
    第 1~4 次失败 -> 401（绝不锁）
    第 5 次失败   -> 423 + Retry-After
    锁定期间用正确密码 -> 仍 423
    登录成功 -> 计数归零（成功 1 次后再错 4 次不锁）
"""

import time
import uuid

import allure
import pytest

from utils.data import DEFAULT_PASSWORD, unique
from utils.schema import validate


def _register(client, username=None, password=DEFAULT_PASSWORD, name=None):
    return client.post(
        "/api/auth/register",
        json={
            "username": username or unique("u"),
            "password": password,
            "name": name or "自动化账号",
        },
    )


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@allure.feature("认证")
@allure.story("注册")
class TestRegister:

    @allure.title("正常注册 -> 201 且不泄露口令哈希")
    @pytest.mark.smoke
    def test_register_ok(self, sut):
        username = unique("reg")
        resp = _register(sut, username, "secret123", "有名有姓")
        assert resp.status_code == 201, resp.text[:300]
        body = resp.json()
        assert body["user"]["username"] == username
        assert body["user"]["role"] == "customer"
        assert "password_hash" not in resp.text and "password_salt" not in resp.text

    @allure.title("重名注册 -> 409 USERNAME_EXISTS")
    def test_register_duplicate(self, sut):
        username = unique("dup")
        assert _register(sut, username).status_code == 201
        resp = _register(sut, username)
        assert resp.status_code == 409
        assert resp.json()["code"] == "USERNAME_EXISTS"
        validate(resp.json(), "error")

    @allure.title("用户名长度边界：2/33 字符 -> 400，3/32 字符 -> 201")
    @pytest.mark.boundary
    @pytest.mark.parametrize(
        "length,expected",
        [(2, 400), (3, 201), (32, 201), (33, 400)],
        ids=["len2", "len3", "len32", "len33"],
    )
    def test_register_username_length(self, sut, length, expected):
        # 201 的那两档要保证唯一（避免与同会话内其他用例撞名），400 的无所谓
        if expected == 201:
            username = ("u" * (length - 2) + uuid.uuid4().hex[:2])[:length]
        else:
            username = "u" * length
        assert len(username) == length
        resp = _register(sut, username)
        assert resp.status_code == expected, resp.text[:200]

    @allure.title("密码长度边界：5/65 字符 -> 400，6/64 字符 -> 201")
    @pytest.mark.boundary
    @pytest.mark.parametrize(
        "length,expected",
        [(5, 400), (6, 201), (64, 201), (65, 400)],
        ids=["len5", "len6", "len64", "len65"],
    )
    def test_register_password_length(self, sut, length, expected):
        resp = _register(sut, unique("pw"), "a" * length)
        assert resp.status_code == expected, resp.text[:200]

    @allure.title("缺字段 / 类型不对 -> 422（不是 400）")
    @pytest.mark.negative
    @pytest.mark.parametrize(
        "payload",
        [
            {"password": "secret123"},
            {"username": unique("t")},
            {"username": 12345678, "password": "secret123"},
            {"username": unique("t"), "password": 123456},
        ],
        ids=["missing-username", "missing-password", "username-int", "password-int"],
    )
    def test_register_format_errors(self, sut, payload):
        resp = sut.post("/api/auth/register", json=payload)
        assert resp.status_code == 422, resp.text[:200]
        assert resp.json()["code"] == "VALIDATION_ERROR"


@allure.feature("认证")
@allure.story("登录")
class TestLogin:

    @allure.title("正确密码 -> 200，返回 Bearer token")
    @pytest.mark.smoke
    def test_login_ok(self, sut, fresh_buyer):
        resp = _login(sut, fresh_buyer["username"], fresh_buyer["password"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_at"] > time.time()

    @allure.title("密码错误 -> 401 BAD_CREDENTIALS")
    @pytest.mark.negative
    def test_login_wrong_password(self, sut, fresh_buyer):
        resp = _login(sut, fresh_buyer["username"], "wrong-password")
        assert resp.status_code == 401
        assert resp.json()["code"] == "BAD_CREDENTIALS"

    @allure.title("用户不存在 -> 401 且错误码与密码错误【完全相同】（防账号枚举）")
    @pytest.mark.security
    def test_login_unknown_user_same_as_wrong_password(self, sut):
        resp = _login(sut, unique("nobody"), "whatever")
        assert resp.status_code == 401
        assert resp.json()["code"] == "BAD_CREDENTIALS"

    @allure.title("profile -> 200，且不泄露口令哈希")
    @pytest.mark.smoke
    def test_profile(self, fresh_buyer):
        resp = fresh_buyer["client"].get("/api/auth/profile")
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == fresh_buyer["username"]
        assert "password_hash" not in resp.text and "password_salt" not in resp.text


@allure.feature("认证")
@allure.story("连续失败锁定")
class TestLockout:

    @allure.title("★ 第 1~4 次失败必须是 401（绝不提前锁定）")
    @pytest.mark.security
    def test_first_four_failures_are_401(self, sut, fresh_buyer):
        for attempt in range(1, 5):
            resp = _login(sut, fresh_buyer["username"], "wrong")
            assert resp.status_code == 401, "第 {} 次就锁定了，off-by-one".format(attempt)
            assert resp.json()["detail"]["failed_attempts"] == attempt

    @allure.title("★ 第 5 次失败 -> 423 ACCOUNT_LOCKED + Retry-After")
    @pytest.mark.security
    def test_fifth_failure_locks(self, sut, fresh_buyer):
        for _ in range(4):
            _login(sut, fresh_buyer["username"], "wrong")
        resp = _login(sut, fresh_buyer["username"], "wrong")
        assert resp.status_code == 423, resp.text[:200]
        assert resp.json()["code"] == "ACCOUNT_LOCKED"
        assert int(resp.headers["Retry-After"]) >= 1

    @allure.title("★ 锁定期间用【正确密码】仍然 423")
    @pytest.mark.security
    def test_correct_password_still_locked(self, sut, fresh_buyer):
        for _ in range(5):
            _login(sut, fresh_buyer["username"], "wrong")
        resp = _login(sut, fresh_buyer["username"], fresh_buyer["password"])
        assert resp.status_code == 423
        assert resp.json()["code"] == "ACCOUNT_LOCKED"
        assert "Retry-After" in resp.headers

    @allure.title("★ 登录成功让失败计数归零（成功 1 次后再错 4 次不锁）")
    @pytest.mark.security
    def test_success_resets_counter(self, sut, fresh_buyer):
        for _ in range(3):
            _login(sut, fresh_buyer["username"], "wrong")
        assert _login(sut, fresh_buyer["username"], fresh_buyer["password"]).status_code == 200

        for attempt in range(1, 5):
            resp = _login(sut, fresh_buyer["username"], "wrong")
            assert resp.status_code == 401, "计数没有归零：第 {} 次就锁了".format(attempt)
            assert resp.json()["detail"]["failed_attempts"] == attempt

    @allure.title("后门解锁后可正常登录")
    def test_unlock_via_hook(self, sut_base_url, sut, fresh_buyer):
        for _ in range(5):
            _login(sut, fresh_buyer["username"], "wrong")
        unlocked = sut.post("/api/_test/user/{}/unlock".format(fresh_buyer["username"]))
        assert unlocked.status_code == 200
        assert _login(sut, fresh_buyer["username"], fresh_buyer["password"]).status_code == 200

    @allure.title("★ 锁定自然到期后可登录，且计数重新开始（不立刻再锁）")
    @pytest.mark.security
    def test_lock_expires_naturally(self, sut, fresh_buyer):
        for _ in range(4):
            _login(sut, fresh_buyer["username"], "wrong")
        locked = _login(sut, fresh_buyer["username"], "wrong")
        assert locked.status_code == 423
        retry_after = int(locked.headers["Retry-After"])

        if retry_after > 5:
            pytest.skip(
                "锁定时长 {}s 过长，无法在用例内等待。"
                "请以 AUTH_LOCK_SECONDS=2 启动 SUT 后重跑（见 CHANGES.md）".format(retry_after)
            )

        time.sleep(retry_after + 1)
        assert _login(sut, fresh_buyer["username"], fresh_buyer["password"]).status_code == 200

        # 到期后计数应归零：第 1 次错误是 401 而不是立刻 423
        again = _login(sut, fresh_buyer["username"], "wrong")
        assert again.status_code == 401, "锁定到期后计数未归零"
        assert again.json()["detail"]["failed_attempts"] == 1


@allure.feature("认证")
@allure.story("token 矩阵")
class TestToken:

    @allure.title("无 Authorization 头 -> 401 UNAUTHORIZED")
    @pytest.mark.security
    def test_no_token(self, sut):
        resp = sut.get("/api/auth/profile")
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHORIZED"

    @allure.title("垃圾 token -> 401")
    @pytest.mark.security
    def test_garbage_token(self, sut):
        assert sut.get("/api/auth/profile", headers={"Authorization": "Bearer garbage"}).status_code == 401

    @allure.title("★ 篡改 token 末字符 -> 401（不是 500）")
    @pytest.mark.security
    def test_tampered_token_not_500(self, fresh_buyer):
        token = fresh_buyer["token"]
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        client = fresh_buyer["client"]
        client.token = tampered
        resp = client.get("/api/auth/profile")
        assert resp.status_code == 401, "篡改 token 竟然返回 {}（应为 401）".format(resp.status_code)

    @allure.title("过期 token -> 401 TOKEN_EXPIRED（后门签发 ttl=-10）")
    @pytest.mark.security
    def test_expired_token(self, sut_base_url, sut, fresh_buyer):
        issued = sut.post(
            "/api/_test/token",
            json={"username": fresh_buyer["username"], "ttl_seconds": -10},
        )
        assert issued.status_code == 200
        resp = sut.get(
            "/api/auth/profile",
            headers={"Authorization": "Bearer " + issued.json()["token"]},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "TOKEN_EXPIRED"

    @allure.title("后门签发的有效 token 可用")
    def test_hook_token_works(self, sut, fresh_buyer):
        issued = sut.post(
            "/api/_test/token",
            json={"username": fresh_buyer["username"], "ttl_seconds": 3600},
        )
        resp = sut.get(
            "/api/auth/profile",
            headers={"Authorization": "Bearer " + issued.json()["token"]},
        )
        assert resp.status_code == 200

    @allure.title("后门签发不存在的用户 -> 404")
    @pytest.mark.negative
    def test_hook_token_unknown_user(self, sut):
        resp = sut.post("/api/_test/token", json={"username": unique("ghost"), "ttl_seconds": 60})
        assert resp.status_code == 404


@allure.feature("认证")
@allure.story("登出与续签")
class TestLogoutRefresh:

    @allure.title("★ logout 是纯 no-op：明确 token_revoked=false，且旧 token 仍可用")
    def test_logout_is_noop(self, fresh_buyer):
        client = fresh_buyer["client"]
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["token_revoked"] is False
        # 语义一致：服务端没有主动失效 token，所以旧 token 依然能用
        assert client.get("/api/auth/profile").status_code == 200

    @allure.title("refresh 旧换新：新 token 可用，旧 token 不做轮换失效")
    def test_refresh(self, fresh_buyer):
        client = fresh_buyer["client"]
        old = client.token
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 200
        new = resp.json()["token"]
        assert new != old

        client.token = new
        assert client.get("/api/auth/profile").status_code == 200

        client.token = old
        assert client.get("/api/auth/profile").status_code == 200, "旧 token 被轮换失效了"

    @allure.title("refresh 不带 token -> 401")
    @pytest.mark.negative
    def test_refresh_without_token(self, sut):
        assert sut.post("/api/auth/refresh").status_code == 401
