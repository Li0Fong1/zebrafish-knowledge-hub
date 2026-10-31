# -*- coding: utf-8 -*-
"""快速入门 / 注册 / 登录：校验、会话、页面入口。"""
from __future__ import annotations

import os
import socket
import sys
import time
import uuid
from pathlib import Path


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


# 项目默认库 3306 没开时，拉起已有的临时库（3307，空密码 root），测完再关。
_STARTED_MYSQL = False
if not _port_open(3306) and not _port_open(3307):
    import subprocess

    _base = Path(os.environ.get("LOCALAPPDATA", "")) / "zfqa_auth_mysql"
    _mysqld = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe")
    if _base.joinpath("mysql").is_dir() and _mysqld.is_file():
        subprocess.Popen(
            [
                str(_mysqld),
                "--no-defaults",
                "--basedir=C:/Program Files/MySQL/MySQL Server 8.0",
                f"--datadir={_base}",
                "--port=3307",
                "--bind-address=127.0.0.1",
                "--mysqlx=0",
            ],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        for _ in range(20):
            time.sleep(0.4)
            if _port_open(3307):
                _STARTED_MYSQL = True
                break

if not _port_open(3306) and _port_open(3307):
    os.environ["MYSQL_HOST"] = "127.0.0.1"
    os.environ["MYSQL_PORT"] = "3307"
    os.environ["MYSQL_USER"] = "root"
    os.environ["MYSQL_PASSWORD"] = ""
    os.environ["MYSQL_DATABASE"] = "zebrafish_db"

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database", ROOT / "api"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from hub_accounts import (  # noqa: E402
    _validate_password,
    _validate_username,
    login_user,
    logout_session,
    register_user,
    user_from_session,
)


def _expect_value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {fn.__name__}{args}")


def test_username_rules():
    assert _validate_username("  tp53  ") == "tp53"
    assert _validate_username("斑马鱼") == "斑马鱼"
    assert _validate_username("_a") == "_a"
    assert _validate_username("a" * 32) == "a" * 32
    _expect_value_error(_validate_username, "a")
    _expect_value_error(_validate_username, "bad name")
    _expect_value_error(_validate_username, "a" * 33)
    _expect_value_error(_validate_username, "ab' OR 1=1")
    _expect_value_error(_validate_username, "ab<script>")
    _expect_value_error(_validate_username, "")


def test_password_rules():
    assert _validate_password("123456") == "123456"
    assert _validate_password("密" * 6) == "密" * 6
    assert _validate_password("x" * 72) == "x" * 72
    _expect_value_error(_validate_password, "12345")
    _expect_value_error(_validate_password, "x" * 73)
    _expect_value_error(_validate_password, "")


def test_page_has_welcome_entry():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    for needle in (
        'id="quickStartBtn"',
        'id="showLoginBtn"',
        'id="showRegisterBtn"',
        'id="hubPass2Input"',
        'id="userAccountBtn"',
        'id="welcomeSwitchBtn"',
    ):
        assert needle in html, needle
    assert "localStorage" in js
    assert "两次输入的密码不一致" in js
    assert "enterAsGuest" in js
    assert "textContent = label" in js
    assert "hubStoreRemove(HUB_SESSION_KEY)" in js
    assert 'id="userLogoutBtn"' in html
    server = (ROOT / "api" / "hub_accounts.py").read_text(encoding="utf-8")
    api = (ROOT / "api" / "api_server.py").read_text(encoding="utf-8")
    assert "LOWER(username)=LOWER(:u)" in server
    assert "set_cookie" in api and "hub_session" in api
    js_re = js.split("HUB_USER_RE = ", 1)[1].split(";", 1)[0].strip()
    assert js_re == r"/^[a-zA-Z0-9_\u4e00-\u9fff]{2,32}$/"


def _cleanup(username: str):
    from sqlalchemy import text

    from mysql_client import MySQLClient

    client = MySQLClient()
    db = next(client.get_db())
    try:
        row = db.execute(
            text("SELECT user_id FROM hub_user WHERE username=:u"),
            {"u": username},
        ).first()
        if row:
            db.execute(
                text("DELETE FROM hub_user_session WHERE user_id=:id"),
                {"id": int(row[0])},
            )
        db.execute(text("DELETE FROM hub_user WHERE username=:u"), {"u": username})
        db.commit()
    finally:
        db.close()


def test_register_login_session():
    username = f"zfqa_t_{uuid.uuid4().hex[:8]}"
    password = "secret1"
    short_name = "测" + username[-1]
    wide = "n" * 32
    extra = (username + "b", username + "c", short_name, wide)
    for name in (username, *extra):
        _cleanup(name)
    try:
        sess = register_user(username, password, "测试员")
        assert len(sess["session_token"]) == 64
        assert sess["user"]["username"] == username
        assert sess["user"]["display_name"] == "测试员"
        me = user_from_session(sess["session_token"])
        assert me and me["username"] == username
        assert user_from_session("") is None
        assert user_from_session("not-a-real-token") is None
        logout_session("")
        logout_session("not-a-real-token")

        try:
            register_user(username, password)
            raise AssertionError("duplicate username should fail")
        except ValueError as e:
            assert "已被注册" in str(e)
        try:
            register_user("  " + username.upper() + "  ", password)
            raise AssertionError("case-insensitive duplicate should fail")
        except ValueError as e:
            assert "已被注册" in str(e)
        try:
            login_user(username, "wrong12")
            raise AssertionError("bad password should fail")
        except ValueError as e:
            assert "错误" in str(e)

        again = login_user(username.upper(), password)
        assert again["session_token"] != sess["session_token"]
        assert again["user"]["username"] == username
        assert user_from_session(again["session_token"])["display_name"] == "测试员"
        logout_session(again["session_token"])
        assert user_from_session(again["session_token"]) is None
        logout_session(again["session_token"])
        assert user_from_session(sess["session_token"])["username"] == username

        blank = register_user(username + "b", "密" * 6, "   ")
        assert blank["user"]["display_name"] == username + "b"
        long_name = "显" * 80
        edged = register_user(username + "c", "x" * 72, long_name)
        assert edged["user"]["display_name"] == "显" * 64
        short_user = register_user(short_name, "123456", None)
        assert short_user["user"]["username"] == short_name
        wide_sess = register_user(wide, "123456")
        assert wide_sess["user"]["username"] == wide

        from sqlalchemy import text
        from mysql_client import MySQLClient

        client = MySQLClient()
        db = next(client.get_db())
        try:
            db.execute(
                text("UPDATE hub_user_session SET expires_at='2000-01-01 00:00:00' WHERE session_token=:t"),
                {"t": sess["session_token"]},
            )
            db.commit()
        finally:
            db.close()
        assert user_from_session(sess["session_token"]) is None
    finally:
        for name in (username, *extra):
            _cleanup(name)


def main() -> int:
    fails = []
    tests = [
        test_username_rules,
        test_password_rules,
        test_page_has_welcome_entry,
        test_register_login_session,
    ]
    for fn in tests:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"PASS {fn.__name__} {time.perf_counter() - t0:.2f}s")
        except Exception as e:
            fails.append(fn.__name__)
            print(f"FAIL {fn.__name__} {type(e).__name__}: {e}")
    print(f"HUB_AUTH_FAILS={len(fails)}")
    if _STARTED_MYSQL:
        import subprocess

        subprocess.run(
            [
                r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqladmin.exe",
                "--host=127.0.0.1",
                "--port=3307",
                "-uroot",
                "shutdown",
            ],
            check=False,
        )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
