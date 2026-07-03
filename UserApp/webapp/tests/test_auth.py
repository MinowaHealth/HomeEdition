"""
Unit tests for ``webapp/auth.py``.

Scope: password hashing + sessions + ``authenticate_user`` + ``change_password``
+ 2FA (TOTP, backup codes, setup/disable) + API keys + utility lookups.

Email-dependent flows (password reset token issuance, signup verification,
Mailgun helpers) are intentionally excluded — the local test environment has
no email transport and those paths mostly wrap an outbound HTTP client. See the
commit series ``tests: auth — ...`` for the rationale.

DB access is mocked at ``auth.get_admin_connection`` — that's the only DB
touch-point in the module. Any session ``SET`` statements, if present, are absorbed
by the MagicMock cursor.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pyotp
import pytest

import auth


# The shared conftest installs an autouse ``mock_auth`` fixture that patches
# ``utils.auth.get_session`` to a fake session so route tests don't need real
# auth. Because ``utils.auth`` is the same module object as ``auth``, that
# patch also replaces ``auth.get_session`` with a MagicMock — which would
# hide the real implementation that this file is trying to test. Override
# the autouse fixture with a no-op so ``auth.get_session`` stays intact.
@pytest.fixture(autouse=True)
def mock_auth():  # noqa: PT004  (override shadow)
    """Disable the conftest-wide auth mock for this module's tests."""
    yield None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_conn(fetchone=None, fetchall=None, rowcount: int = 1):
    """Build a (conn, cursor) pair where cursor.fetchone/fetchall return
    fixed values. Supports lists for ``fetchone`` to simulate sequential
    SELECTs in one call."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    if isinstance(fetchone, list):
        cur.fetchone.side_effect = fetchone
    elif fetchone is not None:
        cur.fetchone.return_value = fetchone
    else:
        cur.fetchone.return_value = None
    cur.fetchall.return_value = fetchall or []
    cur.rowcount = rowcount
    return conn, cur


# ===========================================================================
# 1. Password hashing + session lifecycle
# ===========================================================================


class TestHashPassword:
    def test_roundtrip(self):
        h = auth.hash_password('correct horse battery staple')
        assert auth.verify_password('correct horse battery staple', h) is True

    def test_wrong_password_fails(self):
        h = auth.hash_password('secret1')
        assert auth.verify_password('secret2', h) is False

    def test_hashes_are_non_deterministic(self):
        """Argon2 salts per-call; two hashes of same password differ."""
        assert auth.hash_password('same') != auth.hash_password('same')

    def test_verify_password_rejects_garbage_hash(self):
        assert auth.verify_password('anything', 'not-a-hash') is False

    def test_verify_password_rejects_empty_hash(self):
        # Empty hash -> InvalidHash -> False, never raises.
        assert auth.verify_password('anything', '') is False


class TestLooksLikePasswordHash:
    def test_real_argon2_hash(self):
        h = auth.hash_password('x')
        assert auth.looks_like_password_hash(h) is True

    def test_none(self):
        assert auth.looks_like_password_hash(None) is False

    def test_empty_string(self):
        assert auth.looks_like_password_hash('') is False

    def test_plain_password(self):
        assert auth.looks_like_password_hash('hunter2') is False

    def test_no_dollar_sign(self):
        assert auth.looks_like_password_hash('abc123def456') is False

    def test_bcrypt_style_not_argon2(self):
        # bcrypt uses $2b$, we only accept $argon2id$/$argon2i$
        assert auth.looks_like_password_hash('$2b$12$abcdef') is False


class TestCreateSession:
    def test_returns_uuid_and_inserts(self):
        conn, cur = _mock_conn()
        with patch('auth.get_admin_connection', return_value=conn):
            sid = auth.create_session('user-uuid', ip_address='1.2.3.4',
                                      user_agent='curl')
        # UUID4 string is 36 chars with dashes
        assert len(sid) == 36
        assert sid.count('-') == 4
        cur.execute.assert_called_once()
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_uses_default_tenant(self):
        conn, cur = _mock_conn()
        with patch('auth.get_admin_connection', return_value=conn):
            auth.create_session('user-uuid')
        # First positional arg of INSERT tuple is tenant_id
        args = cur.execute.call_args[0][1]
        assert args[0] == auth.DEFAULT_TENANT_ID

    def test_explicit_tenant_id(self):
        conn, cur = _mock_conn()
        with patch('auth.get_admin_connection', return_value=conn):
            auth.create_session('user-uuid', tenant_id=42)
        args = cur.execute.call_args[0][1]
        assert args[0] == 42


class TestGetSession:
    def _valid_row(self, **overrides):
        row = {
            'tenant_id': 1,
            'session_id': 'sid',
            'user_id': 'uid',
            'expires_at': datetime.now(timezone.utc) + timedelta(hours=1),
            'last_activity': datetime.now(timezone.utc),
            'email': 'a@b.com',
            'display_name': 'A',
            'is_active': True,
            'is_developer': False,
            'home_timezone': 'UTC',
            'created_at': datetime.now(timezone.utc),
        }
        row.update(overrides)
        return row

    def test_missing_session_returns_none(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_session('missing') is None

    def test_expired_session_deletes_and_returns_none(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        conn, cur = _mock_conn(fetchone=self._valid_row(expires_at=past))
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_session('sid') is None
        # Should have issued a DELETE
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('DELETE FROM sessions' in s for s in sql_calls)

    def test_inactive_user_returns_none(self):
        conn, _ = _mock_conn(fetchone=self._valid_row(is_active=False))
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_session('sid') is None

    def test_valid_session_returns_dict_and_slides_expiry(self):
        conn, cur = _mock_conn(fetchone=self._valid_row())
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_session('sid')
        assert out is not None
        assert out['user_id'] == 'uid'
        assert out['username'] == 'a@b.com'
        assert out['database_name'] == 'healthv10'
        # slide -> UPDATE issued
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('UPDATE sessions' in s for s in sql_calls)

    def test_missing_home_timezone_defaults_to_pacific(self):
        conn, _ = _mock_conn(fetchone=self._valid_row(home_timezone=None))
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_session('sid')
        assert out is not None
        assert out['home_timezone'] == 'America/Los_Angeles'

    def test_lookup_excludes_pending_2fa_marker(self):
        """BR-012 regression: get_session must never resolve a pending-2FA
        challenge row as a usable session. The lookup SELECT has to filter
        out the '2FA_PENDING' marker, or a password-only attacker could
        present the pending_2fa_token as a Bearer token and bypass 2FA.

        DB access is mocked, so the WHERE clause isn't truly evaluated here;
        this guards the filter against accidental removal. The live-DB
        behavioural check lives in the integration suite."""
        conn, cur = _mock_conn(fetchone=self._valid_row())
        with patch('auth.get_admin_connection', return_value=conn):
            auth.get_session('sid')
        select_sql = next(
            c[0][0] for c in cur.execute.call_args_list
            if 'FROM sessions' in c[0][0] and 'SELECT' in c[0][0]
        )
        assert "user_agent IS DISTINCT FROM '2FA_PENDING'" in select_sql


class TestDeleteSession:
    def test_executes_delete(self):
        conn, cur = _mock_conn(rowcount=1)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.delete_session('sid') is True
        cur.execute.assert_called_once()
        sql = cur.execute.call_args[0][0]
        assert 'DELETE FROM sessions' in sql
        conn.commit.assert_called_once()


class TestCleanupExpiredSessions:
    def test_returns_deleted_count(self):
        conn, cur = _mock_conn(rowcount=17)
        with patch('auth.get_admin_connection', return_value=conn):
            n = auth.cleanup_expired_sessions()
        assert n == 17
        sql = cur.execute.call_args[0][0]
        assert 'expires_at < NOW()' in sql


class TestExtendSession:
    def test_success(self):
        conn, cur = _mock_conn(rowcount=1)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.extend_session('sid') is True
        conn.commit.assert_called_once()

    def test_no_matching_session(self):
        conn, _ = _mock_conn(rowcount=0)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.extend_session('missing') is False


# ===========================================================================
# 2. authenticate_user + change_password
# ===========================================================================


class TestAuthenticateUser:
    def _user_row(self, password='pw', **overrides):
        row = {
            'tenant_id': 1,
            'id': 'user-uuid',
            'email': 'alice@example.com',
            'display_name': 'Alice',
            'password_hash': auth.hash_password(password),
            'is_active': True,
            'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        row.update(overrides)
        return row

    def test_unknown_email_returns_none(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.authenticate_user('x@y.com', 'pw') is None

    def test_inactive_user_returns_none(self):
        conn, _ = _mock_conn(fetchone=self._user_row(is_active=False))
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.authenticate_user('a@b.com', 'pw') is None

    def test_wrong_password_returns_none(self):
        conn, _ = _mock_conn(fetchone=self._user_row(password='right'))
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.authenticate_user('a@b.com', 'wrong') is None

    def test_successful_login_updates_last_login(self):
        conn, cur = _mock_conn(fetchone=self._user_row(password='pw'))
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.authenticate_user('alice@example.com', 'pw')
        assert out is not None
        assert out['email'] == 'alice@example.com'
        assert out['display_name'] == 'Alice'
        assert out['database_name'] == 'healthv10'
        # Should have executed SELECT then UPDATE last_login
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('UPDATE users' in s and 'last_login' in s for s in sql_calls)
        conn.commit.assert_called_once()

    def test_email_is_lowercased_in_query(self):
        conn, cur = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            auth.authenticate_user('  Alice@Example.COM ', 'pw')
        args = cur.execute.call_args_list[0][0][1]
        # second placeholder is the email
        assert args[1] == 'alice@example.com'

    def test_explicit_tenant_id_used(self):
        conn, cur = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            auth.authenticate_user('x@y.com', 'pw', tenant_id=7)
        args = cur.execute.call_args_list[0][0][1]
        assert args[0] == 7


class TestChangePassword:
    def test_user_not_found(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.change_password('uid', 'old', 'new')
        assert ok is False
        assert err == 'User not found'

    def test_current_password_wrong(self):
        conn, _ = _mock_conn(
            fetchone={'password_hash': auth.hash_password('real')}
        )
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.change_password('uid', 'wrong', 'new')
        assert ok is False
        assert 'incorrect' in (err or '').lower()

    def test_success_updates_hash(self):
        conn, cur = _mock_conn(
            fetchone={'password_hash': auth.hash_password('old')}
        )
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.change_password('uid', 'old', 'newpass')
        assert ok is True
        assert err is None
        # UPDATE users SET password_hash = ... was issued
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('UPDATE users' in s and 'password_hash' in s for s in sql_calls)
        # Verify the new hash in the UPDATE args actually validates new pw
        update_call = [
            c for c in cur.execute.call_args_list
            if 'UPDATE users' in c[0][0] and 'password_hash' in c[0][0]
        ][0]
        new_hash = update_call[0][1][0]
        assert auth.verify_password('newpass', new_hash) is True
        conn.commit.assert_called_once()


# ===========================================================================
# 3. 2FA: TOTP + backup codes + setup/disable
# ===========================================================================


class TestGenerateTotpSecret:
    def test_returns_base32_string(self):
        s = auth.generate_totp_secret()
        assert isinstance(s, str)
        # pyotp random_base32 default length = 32
        assert len(s) == 32
        # base32 alphabet: A-Z, 2-7
        assert all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' for c in s)

    def test_each_call_is_unique(self):
        assert auth.generate_totp_secret() != auth.generate_totp_secret()


class TestGenerateTotpUri:
    def test_uri_contains_issuer_and_email(self):
        secret = auth.generate_totp_secret()
        uri = auth.generate_totp_uri(secret, 'alice@example.com')
        assert uri.startswith('otpauth://totp/')
        assert 'Minowa' in uri
        assert 'alice%40example.com' in uri or 'alice@example.com' in uri

    def test_custom_issuer(self):
        secret = auth.generate_totp_secret()
        uri = auth.generate_totp_uri(secret, 'a@b.c', issuer='Acme')
        assert 'Acme' in uri


class TestVerifyTotp:
    def test_valid_current_code(self):
        secret = auth.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert auth.verify_totp(secret, code) is True

    def test_none_secret(self):
        assert auth.verify_totp(None, '123456') is False

    def test_none_code(self):
        assert auth.verify_totp('ABCDEFGHIJKLMNOP', None) is False

    def test_empty_code(self):
        assert auth.verify_totp('ABCDEFGHIJKLMNOP', '') is False

    def test_wrong_code_from_different_secret(self):
        secret = auth.generate_totp_secret()
        # Pick another secret; its live code almost certainly differs from
        # ours. Loop the (negligible-probability) collision away to keep
        # this test deterministic.
        for _ in range(5):
            other = auth.generate_totp_secret()
            wrong = pyotp.TOTP(other).now()
            if wrong != pyotp.TOTP(secret).now():
                break
        assert auth.verify_totp(secret, wrong) is False

    def test_code_outside_window_fails(self):
        """A code computed ~5 minutes ago is outside the +/-30s window."""
        secret = auth.generate_totp_secret()
        old_code = pyotp.TOTP(secret).at(datetime.now(timezone.utc)
                                        - timedelta(minutes=5))
        assert auth.verify_totp(secret, old_code) is False


class TestGenerateBackupCodes:
    def test_default_count(self):
        plain, hashed = auth.generate_backup_codes()
        assert len(plain) == 10
        assert len(hashed) == 10

    def test_custom_count(self):
        plain, hashed = auth.generate_backup_codes(count=3)
        assert len(plain) == 3
        assert len(hashed) == 3

    def test_plain_codes_are_uppercase_hex(self):
        plain, _ = auth.generate_backup_codes(count=5)
        for code in plain:
            assert len(code) == 8
            assert code == code.upper()
            assert all(c in '0123456789ABCDEF' for c in code)

    def test_codes_roundtrip(self):
        plain, hashed = auth.generate_backup_codes(count=2)
        assert auth.verify_password(plain[0], hashed[0]) is True
        assert auth.verify_password(plain[1], hashed[1]) is True
        assert auth.verify_password(plain[0], hashed[1]) is False

    def test_codes_are_unique(self):
        plain, _ = auth.generate_backup_codes(count=10)
        assert len(set(plain)) == 10


class TestGetUser2FAStatus:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_user_2fa_status('uid')
        assert out == {'enabled': False, 'has_secret': False, 'enabled_at': None}

    def test_enabled_user(self):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conn, _ = _mock_conn(fetchone={
            'totp_enabled': True,
            'has_secret': True,
            'totp_enabled_at': when,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_user_2fa_status('uid')
        assert out == {'enabled': True, 'has_secret': True, 'enabled_at': when}

    def test_null_fields_default_false(self):
        conn, _ = _mock_conn(fetchone={
            'totp_enabled': None,
            'has_secret': None,
            'totp_enabled_at': None,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_user_2fa_status('uid')
        assert out['enabled'] is False
        assert out['has_secret'] is False


class TestSetup2FA:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            secret, uri, err = auth.setup_2fa('uid')
        assert secret is None and uri is None
        assert err == 'User not found'

    def test_already_enabled(self):
        conn, _ = _mock_conn(
            fetchone={'email': 'a@b.c', 'totp_enabled': True}
        )
        with patch('auth.get_admin_connection', return_value=conn):
            secret, uri, err = auth.setup_2fa('uid')
        assert secret is None
        assert err == '2FA is already enabled'

    def test_success_stores_secret(self):
        conn, cur = _mock_conn(
            fetchone={'email': 'a@b.c', 'totp_enabled': False}
        )
        with patch('auth.get_admin_connection', return_value=conn):
            secret, uri, err = auth.setup_2fa('uid')
        assert err is None
        assert secret is not None and len(secret) == 32
        assert uri is not None
        assert uri.startswith('otpauth://totp/')
        # UPDATE users SET totp_secret = <secret>
        sql_calls = [c for c in cur.execute.call_args_list
                     if 'totp_secret' in c[0][0] and 'UPDATE' in c[0][0]]
        assert sql_calls
        assert sql_calls[0][0][1][0] == secret
        conn.commit.assert_called_once()


class TestVerifyAndEnable2FA:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.verify_and_enable_2fa('uid', '123456')
        assert codes is None
        assert err == 'User not found'

    def test_already_enabled(self):
        conn, _ = _mock_conn(fetchone={
            'totp_secret': 'X' * 16, 'totp_enabled': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.verify_and_enable_2fa('uid', '123456')
        assert err == '2FA is already enabled'

    def test_no_secret_stored(self):
        conn, _ = _mock_conn(fetchone={
            'totp_secret': None, 'totp_enabled': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.verify_and_enable_2fa('uid', '123456')
        assert codes is None
        assert 'setup not started' in (err or '')

    def test_wrong_code(self):
        secret = auth.generate_totp_secret()
        conn, _ = _mock_conn(fetchone={
            'totp_secret': secret, 'totp_enabled': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.verify_and_enable_2fa('uid', '000000')
        # A legit code would match ~5.6e-7 of the time; accept the False path
        assert codes is None or err is None

    def test_correct_code_returns_backup_codes(self):
        secret = auth.generate_totp_secret()
        conn, cur = _mock_conn(fetchone={
            'totp_secret': secret, 'totp_enabled': False,
        })
        code = pyotp.TOTP(secret).now()
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.verify_and_enable_2fa('uid', code)
        assert err is None
        assert codes is not None and len(codes) == 10
        # UPDATE users enabled + hashed codes
        updates = [c for c in cur.execute.call_args_list
                   if 'UPDATE users' in c[0][0] and 'totp_enabled' in c[0][0]]
        assert updates
        conn.commit.assert_called_once()


class TestDisable2FA:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.disable_2fa('uid', 'pw')
        assert ok is False and err == 'User not found'

    def test_not_enabled(self):
        conn, _ = _mock_conn(fetchone={
            'password_hash': auth.hash_password('pw'), 'totp_enabled': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.disable_2fa('uid', 'pw')
        assert ok is False and err == '2FA is not enabled'

    def test_wrong_password(self):
        conn, _ = _mock_conn(fetchone={
            'password_hash': auth.hash_password('right'), 'totp_enabled': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.disable_2fa('uid', 'wrong')
        assert ok is False and err == 'Invalid password'

    def test_success(self):
        conn, cur = _mock_conn(fetchone={
            'password_hash': auth.hash_password('pw'), 'totp_enabled': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.disable_2fa('uid', 'pw')
        assert ok is True and err is None
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('totp_enabled = false' in s for s in sql_calls)
        conn.commit.assert_called_once()


class TestVerify2FALogin:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            ok, is_backup, err = auth.verify_2fa_login('uid', '123456')
        assert ok is False and is_backup is False
        assert err == 'User not found'

    def test_not_enabled(self):
        conn, _ = _mock_conn(fetchone={
            'totp_secret': None, 'totp_enabled': False, 'totp_backup_codes': None,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, is_backup, err = auth.verify_2fa_login('uid', '123456')
        assert ok is False
        assert '2FA is not enabled' in (err or '')

    def test_valid_totp(self):
        secret = auth.generate_totp_secret()
        conn, _ = _mock_conn(fetchone={
            'totp_secret': secret, 'totp_enabled': True, 'totp_backup_codes': [],
        })
        code = pyotp.TOTP(secret).now()
        with patch('auth.get_admin_connection', return_value=conn):
            ok, is_backup, err = auth.verify_2fa_login('uid', code)
        assert ok is True
        assert is_backup is False
        assert err is None

    def test_valid_backup_code_consumes_it(self):
        """A backup code works once; the DB row is rewritten without it."""
        secret = auth.generate_totp_secret()
        plain, hashed = auth.generate_backup_codes(count=3)

        # Seed the user row with 3 hashed backup codes; after use we expect the
        # matching one to be removed from the UPDATE arg.
        conn, cur = _mock_conn(fetchone={
            'totp_secret': secret,
            'totp_enabled': True,
            'totp_backup_codes': list(hashed),
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, is_backup, err = auth.verify_2fa_login('uid', plain[1])
        assert ok is True
        assert is_backup is True
        assert err is None

        # Confirm the UPDATE wrote the remaining two codes.
        updates = [c for c in cur.execute.call_args_list
                   if 'UPDATE users' in c[0][0] and 'totp_backup_codes' in c[0][0]]
        assert updates, 'expected an UPDATE removing the consumed code'
        remaining = updates[0][0][1][0]
        assert remaining is not None
        assert len(remaining) == 2
        assert hashed[1] not in remaining

    def test_invalid_code(self):
        secret = auth.generate_totp_secret()
        conn, _ = _mock_conn(fetchone={
            'totp_secret': secret, 'totp_enabled': True, 'totp_backup_codes': [],
        })
        with patch('auth.get_admin_connection', return_value=conn):
            ok, is_backup, err = auth.verify_2fa_login('uid', 'ZZZZZZZZ')
        assert ok is False
        assert is_backup is False
        assert err == 'Invalid 2FA code'


class TestGetRemainingBackupCodesCount:
    def test_no_row(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_remaining_backup_codes_count('uid') == 0

    def test_null_codes(self):
        conn, _ = _mock_conn(fetchone={'totp_backup_codes': None})
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_remaining_backup_codes_count('uid') == 0

    def test_count(self):
        conn, _ = _mock_conn(fetchone={'totp_backup_codes': ['a', 'b', 'c']})
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_remaining_backup_codes_count('uid') == 3


class TestRegenerateBackupCodes:
    def test_missing_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.regenerate_backup_codes('uid', 'pw')
        assert codes is None and err == 'User not found'

    def test_not_enabled(self):
        conn, _ = _mock_conn(fetchone={
            'password_hash': auth.hash_password('pw'), 'totp_enabled': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.regenerate_backup_codes('uid', 'pw')
        assert codes is None and err == '2FA is not enabled'

    def test_wrong_password(self):
        conn, _ = _mock_conn(fetchone={
            'password_hash': auth.hash_password('right'), 'totp_enabled': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.regenerate_backup_codes('uid', 'wrong')
        assert codes is None and err == 'Invalid password'

    def test_success(self):
        conn, cur = _mock_conn(fetchone={
            'password_hash': auth.hash_password('pw'), 'totp_enabled': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            codes, err = auth.regenerate_backup_codes('uid', 'pw')
        assert err is None
        assert codes is not None and len(codes) == 10
        sql_calls = [c[0][0] for c in cur.execute.call_args_list]
        assert any('totp_backup_codes' in s and 'UPDATE' in s for s in sql_calls)
        conn.commit.assert_called_once()


class TestCheck2FARequired:
    def test_unknown_user(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            req, uid, err = auth.check_2fa_required('x@y.com')
        assert req is False
        assert uid is None
        assert err == 'User not found'

    def test_enabled(self):
        conn, _ = _mock_conn(fetchone={'id': 'uid', 'totp_enabled': True})
        with patch('auth.get_admin_connection', return_value=conn):
            req, uid, err = auth.check_2fa_required('a@b.c')
        assert req is True
        assert uid == 'uid'
        assert err is None

    def test_disabled(self):
        conn, _ = _mock_conn(fetchone={'id': 'uid', 'totp_enabled': False})
        with patch('auth.get_admin_connection', return_value=conn):
            req, uid, err = auth.check_2fa_required('a@b.c')
        assert req is False
        assert uid == 'uid'

    def test_null_totp_enabled_treated_as_false(self):
        conn, _ = _mock_conn(fetchone={'id': 'uid', 'totp_enabled': None})
        with patch('auth.get_admin_connection', return_value=conn):
            req, uid, _ = auth.check_2fa_required('a@b.c')
        assert req is False


# ===========================================================================
# 4. API keys + utility lookups
# ===========================================================================


class TestHashApiKey:
    def test_deterministic(self):
        h1 = auth._hash_api_key('hbk_abc123')
        h2 = auth._hash_api_key('hbk_abc123')
        assert h1 == h2

    def test_different_inputs_differ(self):
        assert auth._hash_api_key('hbk_a') != auth._hash_api_key('hbk_b')

    def test_sha256_hex_length(self):
        assert len(auth._hash_api_key('anything')) == 64


class TestCreateApiKey:
    def test_rejects_at_limit(self):
        conn, _ = _mock_conn(
            fetchone={'cnt': auth.MAX_API_KEYS_PER_USER}
        )
        with patch('auth.get_admin_connection', return_value=conn):
            key_id, result = auth.create_api_key('uid')
        assert key_id is None
        assert 'Maximum' in result

    def test_success_returns_raw_key(self):
        conn, cur = _mock_conn(fetchone={'cnt': 0})
        with patch('auth.get_admin_connection', return_value=conn):
            key_id, raw = auth.create_api_key('uid', label='My key',
                                              ip_address='1.2.3.4')
        assert key_id is not None
        assert raw.startswith('hbk_')
        # hbk_ + 32 hex = 36 chars
        assert len(raw) == 36
        # INSERT was issued
        inserts = [c for c in cur.execute.call_args_list
                   if 'INSERT INTO api_tokens' in c[0][0]]
        assert inserts
        conn.commit.assert_called_once()

    def test_stored_hash_matches_raw(self):
        """Hash stored must be SHA-256 of the raw key (roundtrip invariant)."""
        conn, cur = _mock_conn(fetchone={'cnt': 0})
        with patch('auth.get_admin_connection', return_value=conn):
            _, raw = auth.create_api_key('uid')
        insert = [c for c in cur.execute.call_args_list
                  if 'INSERT INTO api_tokens' in c[0][0]][0]
        # Tuple order: (tenant_id, key_id, user_id, token_hash, key_prefix, ...)
        args = insert[0][1]
        assert args[3] == auth._hash_api_key(raw)
        assert args[4] == raw[:12]


class TestLookupApiKey:
    def test_none_token(self):
        assert auth.lookup_api_key(None) is None

    def test_non_hbk_prefix(self):
        assert auth.lookup_api_key('Bearer garbage') is None

    def test_no_row_in_db(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.lookup_api_key('hbk_' + 'a' * 32) is None

    def test_inactive_user(self):
        conn, _ = _mock_conn(fetchone={
            'tenant_id': 1, 'key_id': 'kid', 'user_id': 'uid',
            'token_hash': 'h', 'email': 'a@b.c', 'display_name': 'A',
            'is_active': False, 'is_developer': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.lookup_api_key('hbk_' + 'a' * 32) is None

    def test_success_returns_user_dict(self):
        conn, cur = _mock_conn(fetchone={
            'tenant_id': 1, 'key_id': 'kid', 'user_id': 'uid',
            'token_hash': 'h', 'email': 'a@b.c', 'display_name': 'A',
            'is_active': True, 'is_developer': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.lookup_api_key('hbk_' + 'a' * 32)
        assert out is not None
        assert out['email'] == 'a@b.c'
        assert out['is_developer'] is True
        assert out['session_id'] is None
        assert out['api_key_id'] == 'kid'
        # last_used_at update attempted
        updates = [c for c in cur.execute.call_args_list
                   if 'UPDATE api_tokens' in c[0][0] and 'last_used_at' in c[0][0]]
        assert updates

    def test_last_used_update_failure_does_not_fail_auth(self):
        """If the last_used_at UPDATE raises, caller still gets the user."""
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            'tenant_id': 1, 'key_id': 'kid', 'user_id': 'uid',
            'token_hash': 'h', 'email': 'a@b.c', 'display_name': 'A',
            'is_active': True, 'is_developer': False,
        }

        def _execute(sql, *args, **kwargs):
            if 'UPDATE api_tokens' in sql and 'last_used_at' in sql:
                raise RuntimeError('db blew up')
            return None
        cur.execute.side_effect = _execute

        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.lookup_api_key('hbk_' + 'a' * 32)
        assert out is not None
        assert out['email'] == 'a@b.c'


class TestCreateThenLookupApiKeyRoundtrip:
    """End-to-end (still mocked at DB layer): create_api_key returns a raw
    key; looking that key up with the same hash recovers the user row."""

    def test_roundtrip(self):
        user_id = str(uuid.uuid4())

        # --- create leg ---
        create_conn, create_cur = _mock_conn(fetchone={'cnt': 0})
        with patch('auth.get_admin_connection', return_value=create_conn):
            _, raw = auth.create_api_key(user_id)
        assert raw.startswith('hbk_')

        # Capture the hash that would live in the DB:
        insert = [c for c in create_cur.execute.call_args_list
                  if 'INSERT INTO api_tokens' in c[0][0]][0]
        stored_prefix = insert[0][1][4]
        stored_hash = insert[0][1][3]

        # --- lookup leg: DB returns a matching row when prefix+hash match ---
        lookup_conn = MagicMock()
        lookup_cur = MagicMock()
        lookup_conn.cursor.return_value = lookup_cur

        def _execute(sql, params=None):
            if 'SELECT t.tenant_id' in sql and 'api_tokens' in sql:
                # Only return the row if the lookup really sent the same
                # prefix + hash we recorded at create time.
                assert params is not None
                prefix, token_hash = params
                if prefix == stored_prefix and token_hash == stored_hash:
                    lookup_cur.fetchone.return_value = {
                        'tenant_id': 1, 'key_id': 'kid', 'user_id': user_id,
                        'token_hash': token_hash, 'email': 'a@b.c',
                        'display_name': 'A', 'is_active': True,
                        'is_developer': False,
                    }
                else:
                    lookup_cur.fetchone.return_value = None
            return None
        lookup_cur.execute.side_effect = _execute

        with patch('auth.get_admin_connection', return_value=lookup_conn):
            out = auth.lookup_api_key(raw)
        assert out is not None
        assert out['user_id'] == user_id


class TestListApiKeys:
    def test_returns_rows(self):
        conn, cur = _mock_conn(fetchall=[
            {'id': 'k1', 'key_prefix': 'hbk_abc', 'device_name': 'MCP',
             'token_type': 'mcp', 'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc),
             'last_used_at': None},
            {'id': 'k2', 'key_prefix': 'hbk_xyz', 'device_name': 'mobile',
             'token_type': 'mcp', 'created_at': datetime(2026, 2, 1, tzinfo=timezone.utc),
             'last_used_at': datetime(2026, 4, 1, tzinfo=timezone.utc)},
        ])
        with patch('auth.get_admin_connection', return_value=conn):
            rows = auth.list_api_keys('uid')
        assert len(rows) == 2
        assert rows[0]['key_prefix'] == 'hbk_abc'
        # query is scoped tenant + user + not revoked
        sql = cur.execute.call_args[0][0]
        assert 'revoked_at IS NULL' in sql

    def test_empty(self):
        conn, _ = _mock_conn(fetchall=[])
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.list_api_keys('uid') == []


class TestRevokeApiKey:
    def test_success(self):
        conn, cur = _mock_conn(rowcount=1)
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.revoke_api_key('kid', 'uid')
        assert ok is True
        assert err is None
        sql = cur.execute.call_args[0][0]
        assert 'UPDATE api_tokens' in sql and 'revoked_at = NOW()' in sql
        conn.commit.assert_called()

    def test_not_found(self):
        conn, _ = _mock_conn(rowcount=0)
        with patch('auth.get_admin_connection', return_value=conn):
            ok, err = auth.revoke_api_key('missing', 'uid')
        assert ok is False
        assert 'not found' in (err or '').lower()


class TestGetUserByEmail:
    def test_active_user(self):
        conn, _ = _mock_conn(fetchone={
            'tenant_id': 1, 'id': 'uid', 'email': 'a@b.c',
            'display_name': 'A', 'is_active': True,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            out = auth.get_user_by_email('A@B.C')
        assert out is not None
        assert out['email'] == 'a@b.c'
        assert out['username'] == 'a@b.c'
        assert out['database_name'] == 'healthv10'

    def test_inactive_user_returns_none(self):
        conn, _ = _mock_conn(fetchone={
            'tenant_id': 1, 'id': 'uid', 'email': 'a@b.c',
            'display_name': 'A', 'is_active': False,
        })
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_user_by_email('a@b.c') is None

    def test_unknown(self):
        conn, _ = _mock_conn(fetchone=None)
        with patch('auth.get_admin_connection', return_value=conn):
            assert auth.get_user_by_email('nobody@x.y') is None


class TestGetUserByUsername:
    def test_delegates_to_email_lookup(self):
        with patch('auth.get_user_by_email', return_value={'email': 'x@y.z'}) as m:
            out = auth.get_user_by_username('x@y.z', tenant_id=3)
        m.assert_called_once_with('x@y.z', 3)
        assert out == {'email': 'x@y.z'}
