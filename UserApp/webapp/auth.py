"""
Authentication module for the healthv10 Home Edition system
Handles password hashing, session management, and user validation

In healthv10 Home Edition:
  - Single shared database (healthv10); privacy enforced at the
    application level (no RLS)
  - tenant_id column on all tables (always 1), included in composite
    primary keys — the data model matches the enterprise schema
  - Auth queries filter by tenant_id and email/user_id explicitly
"""

from __future__ import annotations

import uuid
import re
import os
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
import db_driver

# Session configuration - can be overridden via environment variable
# SESSION_TIMEOUT_MINUTES takes precedence over SESSION_DURATION_HOURS
_timeout_minutes = os.getenv('SESSION_TIMEOUT_MINUTES')
if _timeout_minutes:
    SESSION_DURATION_HOURS = float(_timeout_minutes) / 60
else:
    SESSION_DURATION_HOURS = int(os.getenv('SESSION_DURATION_HOURS', '24'))
# Argon2 hash pattern: $argon2id$v=19$m=65540,t=3,p=4$salt$hash
PASSWORD_HASH_RE = re.compile(r'^\$argon2[id]{1,2}\$', re.IGNORECASE)
# Initialize password hasher
ph = PasswordHasher()

# Default tenant for single-tenant deployments
DEFAULT_TENANT_ID = int(os.getenv('DEFAULT_TENANT_ID', '1'))

def get_admin_connection() -> Any:
    """
    Get a direct connection for authentication operations.

    Home Edition: uses the same app role (APP_DB_USER) as all other
    connections — there is no superuser path. The name is kept so
    existing call sites stay unchanged.
    """
    return db_driver.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'healthv10'),
        user=os.getenv('APP_DB_USER', 'healthv10_app'),
        password=os.environ['APP_DB_PASSWORD'],
    )


def hash_password(password: str) -> str:
    """Hash a password for storage using Argon2id"""
    return ph.hash(password)

def looks_like_password_hash(value: str | None) -> bool:
    """Best-effort check to avoid double-hashing existing password hashes."""
    if not value or '$' not in value:
        return False
    return bool(PASSWORD_HASH_RE.match(value))

def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its Argon2id hash"""
    try:
        ph.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False

def authenticate_user(email: str, password: str, tenant_id: int | None = None) -> dict[str, Any] | None:
    """
    Authenticate a user with email and password within a tenant.
    Returns user dict if successful, None if failed.

    Args:
        email: User's email address
        password: Plain text password
        tenant_id: Tenant ID to authenticate within (defaults to DEFAULT_TENANT_ID)
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT tenant_id, id, email, display_name, password_hash, is_active, created_at
            FROM users
            WHERE tenant_id = %s AND email = %s
        """, (tenant_id, email.lower().strip(),))

        user = cur.fetchone()

        if not user:
            return None

        if not user['is_active']:
            return None

        # Verify password
        if not verify_password(password, user['password_hash']):
            return None

        # Update last login
        cur.execute("""
            UPDATE users
            SET last_login = NOW()
            WHERE tenant_id = %s AND id = %s
        """, (tenant_id, user['id'],))
        conn.commit()

        return {
            'tenant_id': user['tenant_id'],
            'id': user['id'],
            'username': user.get('email'),  # Use email as username
            'email': user['email'],
            'display_name': user['display_name'],
            'database_name': 'healthv10',  # For backwards compatibility
            'created_at': user['created_at'].isoformat() if user.get('created_at') else None
        }

    finally:
        conn.close()

def create_session(user_id: str, ip_address: str | None = None, user_agent: str | None = None, tenant_id: int | None = None) -> str:
    """
    Create a new session for a user within a tenant.
    Returns session_id

    Args:
        user_id: UUID of the user
        ip_address: Client IP address
        user_agent: Client user agent string
        tenant_id: Tenant ID (defaults to DEFAULT_TENANT_ID)
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        session_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)

        cur.execute("""
            INSERT INTO sessions (tenant_id, session_id, user_id, created_at, expires_at, ip_address, user_agent, last_activity)
            VALUES (%s, %s, %s, NOW(), %s, %s, %s, NOW())
        """, (tenant_id, session_id, user_id, expires_at, ip_address, user_agent))

        conn.commit()
        return session_id

    finally:
        conn.close()

def get_session(session_id: str) -> dict[str, Any] | None:
    """
    Get session information.
    Returns user dict if session is valid, None if invalid/expired.

    Note: Session lookup is by session_id only (UUID is globally unique),
    but the returned dict includes tenant_id for query scoping.
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # `user_agent IS DISTINCT FROM '2FA_PENDING'` excludes pending-2FA
        # challenge rows (inserted by /login before the second factor is
        # checked). They live in this table but must never resolve as a
        # usable session, or a password-only attacker could present the
        # pending_2fa_token as a Bearer token and bypass 2FA. See BR-012.
        # IS DISTINCT FROM (not <>) so legitimate NULL user_agent still matches.
        cur.execute("""
            SELECT s.tenant_id, s.session_id, s.user_id, s.expires_at, s.last_activity,
                   u.email, u.display_name, u.is_active, u.is_developer, u.home_timezone,
                   u.unit_system, u.created_at
            FROM sessions s
            JOIN users u ON s.tenant_id = u.tenant_id AND s.user_id = u.id
            WHERE s.session_id = %s
              AND s.user_agent IS DISTINCT FROM '2FA_PENDING'
        """, (session_id,))

        session = cur.fetchone()

        if not session:
            return None

        # Check if expired
        if session['expires_at'] < datetime.now(timezone.utc):
            # Delete expired session
            cur.execute("DELETE FROM sessions WHERE tenant_id = %s AND session_id = %s",
                       (session['tenant_id'], session_id,))
            conn.commit()
            return None

        # Check if user is still active
        if not session['is_active']:
            return None

        # Update last activity and slide session expiry window
        new_expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)
        cur.execute("""
            UPDATE sessions
            SET last_activity = NOW(), expires_at = %s
            WHERE tenant_id = %s AND session_id = %s
        """, (new_expires_at, session['tenant_id'], session_id,))
        conn.commit()

        return {
            'tenant_id': session['tenant_id'],
            'session_id': session['session_id'],
            'user_id': session['user_id'],
            'username': session['email'],  # Use email as username
            'email': session['email'],
            'display_name': session['display_name'],
            'home_timezone': session['home_timezone'] or 'America/Los_Angeles',
            'unit_system': session['unit_system'] or 'imperial',
            'is_developer': session.get('is_developer', False),
            'database_name': 'healthv10',  # For backwards compatibility
            'created_at': session['created_at'].isoformat() if session.get('created_at') else None
        }

    finally:
        conn.close()

def delete_session(session_id: str) -> bool:
    """Delete a session (logout)"""
    conn = get_admin_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def cleanup_expired_sessions() -> int:
    """Remove all expired sessions"""
    conn = get_admin_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM sessions WHERE expires_at < NOW()")
        deleted_count = cur.rowcount
        conn.commit()
        return deleted_count
    finally:
        conn.close()

def extend_session(session_id: str) -> bool:
    """Extend session expiration by SESSION_DURATION_HOURS"""
    conn = get_admin_connection()
    cur = conn.cursor()
    
    try:
        new_expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)
        cur.execute("""
            UPDATE sessions
            SET expires_at = %s, last_activity = NOW()
            WHERE session_id = %s
        """, (new_expires_at, session_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def change_password(user_id: str, current_password: str, new_password: str, tenant_id: int | None = None) -> tuple[bool, str | None]:
    """
    Change password for authenticated user.
    Returns (success, error_message)

    Args:
        user_id: UUID of the user
        current_password: Current password to verify
        new_password: New password to set
        tenant_id: Tenant ID (defaults to DEFAULT_TENANT_ID)
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Get current password hash
        cur.execute(
            "SELECT password_hash FROM users WHERE tenant_id = %s AND id = %s AND is_active = true",
            (tenant_id, user_id,)
        )
        user = cur.fetchone()

        if not user:
            return False, "User not found"

        # Verify current password
        if not verify_password(current_password, user['password_hash']):
            return False, "Current password is incorrect"

        # Update to new password
        new_hash = hash_password(new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE tenant_id = %s AND id = %s",
            (new_hash, tenant_id, user_id)
        )
        conn.commit()

        return True, None

    finally:
        conn.close()


def set_unit_system(user_id: str, unit_system: str, tenant_id: int | None = None) -> bool:
    """Set the user's display unit system ('imperial' or 'metric').

    Returns True if a row was updated, False if the user wasn't found.
    Caller validates the value; the DB CHECK constraint is the backstop.
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET unit_system = %s, updated_at = NOW() WHERE tenant_id = %s AND id = %s",
            (unit_system, tenant_id, user_id)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_user_by_email(email: str, tenant_id: int | None = None) -> dict[str, Any] | None:
    """Fetch active user record by email from healthv10."""
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT tenant_id, id, email, display_name, is_active
            FROM users
            WHERE tenant_id = %s AND email = %s
            """,
            (tenant_id, email.lower(),),
        )
        user = cur.fetchone()
        if not user or not user['is_active']:
            return None
        result = dict(user)
        result['username'] = result['email']  # Use email as username
        result['database_name'] = 'healthv10'  # Always healthv10
        return result
    finally:
        conn.close()


def get_user_by_username(identifier: str, tenant_id: int | None = None) -> dict[str, Any] | None:
    """
    Fetch active user by username/email identifier.
    In v10, we look up by email since there's no username column.
    """
    return get_user_by_email(identifier, tenant_id)


# ============================================================================
# Two-Factor Authentication (TOTP)
# ============================================================================

import secrets
import base64

# pyotp is required: pip install pyotp
try:
    import pyotp
    TOTP_AVAILABLE = True
except ImportError:
    TOTP_AVAILABLE = False


def generate_totp_secret() -> str:
    """
    Generate a new TOTP secret key for 2FA setup.
    Returns a base32-encoded secret string.
    """
    if not TOTP_AVAILABLE:
        raise RuntimeError("pyotp not installed - run: pip install pyotp")
    return pyotp.random_base32()


def generate_totp_uri(secret: str, email: str, issuer: str = "Minowa") -> str:
    """
    Generate a TOTP URI for QR code generation.
    This URI can be used by authenticator apps like Google Authenticator.
    """
    if not TOTP_AVAILABLE:
        raise RuntimeError("pyotp not installed - run: pip install pyotp")
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str | None, code: str | None) -> bool:
    """
    Verify a TOTP code against a secret.
    Allows 1 window of tolerance (30 seconds before/after).
    Returns True if valid, False otherwise.
    """
    if not TOTP_AVAILABLE:
        raise RuntimeError("pyotp not installed - run: pip install pyotp")
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    # valid_window=1 allows codes from 30s ago or 30s in future
    return totp.verify(code, valid_window=1)


def generate_backup_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """
    Generate a set of single-use backup codes.
    Returns (plain_codes, hashed_codes) tuple.
    plain_codes are shown to user once; hashed_codes are stored in DB.
    """
    plain_codes = []
    hashed_codes = []

    for _ in range(count):
        # Generate 8-character alphanumeric code (easy to type)
        code = secrets.token_hex(4).upper()  # e.g., "A1B2C3D4"
        plain_codes.append(code)
        hashed_codes.append(hash_password(code))

    return plain_codes, hashed_codes


def get_user_2fa_status(user_id: str) -> dict[str, Any]:
    """
    Get 2FA status for a user.
    Returns dict with 'enabled', 'has_secret', 'enabled_at'.
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT totp_enabled, totp_secret IS NOT NULL as has_secret, totp_enabled_at
            FROM users
            WHERE id = %s
        """, (user_id,))
        result = cur.fetchone()

        if not result:
            return {'enabled': False, 'has_secret': False, 'enabled_at': None}

        return {
            'enabled': result['totp_enabled'] or False,
            'has_secret': result['has_secret'] or False,
            'enabled_at': result['totp_enabled_at']
        }
    finally:
        conn.close()


def setup_2fa(user_id: str) -> tuple[str | None, str | None, str | None]:
    """
    Start 2FA setup: generate and store a secret (not yet enabled).
    Returns (secret, uri, error_message).
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Get user email for the URI
        cur.execute("SELECT email, totp_enabled FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

        if not user:
            return None, None, "User not found"

        if user['totp_enabled']:
            return None, None, "2FA is already enabled"

        # Generate new secret
        secret = generate_totp_secret()
        uri = generate_totp_uri(secret, user['email'])

        # Store secret (but don't enable yet - user must verify first)
        cur.execute("""
            UPDATE users
            SET totp_secret = %s
            WHERE id = %s
        """, (secret, user_id))
        conn.commit()

        return secret, uri, None

    except Exception as e:
        conn.rollback()
        return None, None, str(e)
    finally:
        conn.close()


def verify_and_enable_2fa(user_id: str, code: str) -> tuple[list[str] | None, str | None]:
    """
    Verify a TOTP code and enable 2FA if correct.
    Generates and returns backup codes on success.
    Returns (backup_codes, error_message).
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Get the pending secret
        cur.execute("""
            SELECT totp_secret, totp_enabled
            FROM users
            WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()

        if not user:
            return None, "User not found"

        if user['totp_enabled']:
            return None, "2FA is already enabled"

        if not user['totp_secret']:
            return None, "2FA setup not started - call setup first"

        # Verify the code
        if not verify_totp(user['totp_secret'], code):
            return None, "Invalid verification code"

        # Generate backup codes
        plain_codes, hashed_codes = generate_backup_codes()

        # Enable 2FA and store backup codes
        cur.execute("""
            UPDATE users
            SET totp_enabled = true,
                totp_enabled_at = NOW(),
                totp_backup_codes = %s
            WHERE id = %s
        """, (hashed_codes, user_id))
        conn.commit()

        return plain_codes, None

    except Exception as e:
        conn.rollback()
        return None, str(e)
    finally:
        conn.close()


def disable_2fa(user_id: str, password: str) -> tuple[bool, str | None]:
    """
    Disable 2FA for a user (requires password confirmation).
    Returns (success, error_message).
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Get user and verify password
        cur.execute("""
            SELECT password_hash, totp_enabled
            FROM users
            WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()

        if not user:
            return False, "User not found"

        if not user['totp_enabled']:
            return False, "2FA is not enabled"

        if not verify_password(password, user['password_hash']):
            return False, "Invalid password"

        # Disable 2FA (keep secret in case they re-enable)
        cur.execute("""
            UPDATE users
            SET totp_enabled = false,
                totp_backup_codes = NULL
            WHERE id = %s
        """, (user_id,))
        conn.commit()

        return True, None

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def verify_2fa_login(user_id: str, code: str) -> tuple[bool, bool, str | None]:
    """
    Verify a 2FA code during login.
    Accepts either TOTP code or backup code.
    Returns (success, is_backup_code, error_message).
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT totp_secret, totp_enabled, totp_backup_codes
            FROM users
            WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()

        if not user:
            return False, False, "User not found"

        if not user['totp_enabled']:
            return False, False, "2FA is not enabled for this user"

        # First, try TOTP verification
        if verify_totp(user['totp_secret'], code):
            return True, False, None

        # If TOTP fails, try backup codes
        backup_codes = user['totp_backup_codes'] or []
        for i, hashed_code in enumerate(backup_codes):
            if verify_password(code.upper(), hashed_code):
                # Remove the used backup code
                new_codes = backup_codes[:i] + backup_codes[i+1:]
                cur.execute("""
                    UPDATE users
                    SET totp_backup_codes = %s
                    WHERE id = %s
                """, (new_codes if new_codes else None, user_id))
                conn.commit()
                return True, True, None

        return False, False, "Invalid 2FA code"

    except Exception as e:
        conn.rollback()
        return False, False, str(e)
    finally:
        conn.close()


def get_remaining_backup_codes_count(user_id: str) -> int:
    """Get the number of remaining backup codes for a user."""
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT totp_backup_codes
            FROM users
            WHERE id = %s
        """, (user_id,))
        result = cur.fetchone()

        if not result or not result['totp_backup_codes']:
            return 0

        return len(result['totp_backup_codes'])
    finally:
        conn.close()


def regenerate_backup_codes(user_id: str, password: str) -> tuple[list[str] | None, str | None]:
    """
    Regenerate backup codes (requires password confirmation).
    Returns (new_codes, error_message).
    """
    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Verify password
        cur.execute("""
            SELECT password_hash, totp_enabled
            FROM users
            WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()

        if not user:
            return None, "User not found"

        if not user['totp_enabled']:
            return None, "2FA is not enabled"

        if not verify_password(password, user['password_hash']):
            return None, "Invalid password"

        # Generate new backup codes
        plain_codes, hashed_codes = generate_backup_codes()

        cur.execute("""
            UPDATE users
            SET totp_backup_codes = %s
            WHERE id = %s
        """, (hashed_codes, user_id))
        conn.commit()

        return plain_codes, None

    except Exception as e:
        conn.rollback()
        return None, str(e)
    finally:
        conn.close()


def check_2fa_required(email: str, tenant_id: int | None = None) -> tuple[bool, str | None, str | None]:
    """
    Check if 2FA is required for login (called after password verification).
    Returns (required, user_id, error).
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, totp_enabled
            FROM users
            WHERE tenant_id = %s AND email = %s AND is_active = true
        """, (tenant_id, email.lower().strip(),))
        user = cur.fetchone()

        if not user:
            return False, None, "User not found"

        return user['totp_enabled'] or False, user['id'], None
    finally:
        conn.close()


# ============================================================================
# API Keys (Long-lived tokens for MCP, mobile, integrations)
# Phase 2 of auth migration — see DataModel3/Authentication.md
# ============================================================================

import hashlib

# Maximum active API keys per user
MAX_API_KEYS_PER_USER = int(os.getenv('MAX_API_KEYS_PER_USER', '5'))


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 hash for API key storage. Appropriate for high-entropy tokens."""
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def create_api_key(user_id: str, tenant_id: int | None = None, label: str = 'MCP', ip_address: str | None = None) -> tuple[str | None, str]:
    """
    Create a new long-lived API key for a user.

    Returns (key_id, raw_key) on success, (None, error_message) on failure.
    The raw_key is shown once and never stored — caller must display it immediately.

    Args:
        user_id: UUID of the user
        tenant_id: Tenant ID (defaults to DEFAULT_TENANT_ID)
        label: Human-readable label (e.g. "My MCP key")
        ip_address: Client IP address for audit
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Check active key count
        cur.execute("""
            SELECT COUNT(*) as cnt FROM api_tokens
            WHERE tenant_id = %s AND user_id = %s AND revoked_at IS NULL
        """, (tenant_id, user_id))
        row = cur.fetchone()
        count = row['cnt'] if row else 0
        if count >= MAX_API_KEYS_PER_USER:
            return None, f"Maximum of {MAX_API_KEYS_PER_USER} active API keys reached"

        # Generate key: hbk_ + 32 hex chars (128 bits of entropy)
        raw_key = 'hbk_' + secrets.token_hex(16)
        key_prefix = raw_key[:12]
        token_hash = _hash_api_key(raw_key)
        key_id = str(uuid.uuid4())

        cur.execute("""
            INSERT INTO api_tokens
                (tenant_id, id, user_id, token_hash, key_prefix, device_name,
                 token_type, created_at, created_ip)
            VALUES (%s, %s, %s, %s, %s, %s, 'mcp', NOW(), %s)
        """, (tenant_id, key_id, user_id, token_hash, key_prefix, label, ip_address))

        conn.commit()
        return key_id, raw_key

    except Exception as e:
        conn.rollback()
        return None, str(e)
    finally:
        conn.close()


def lookup_api_key(bearer_token: str | None) -> dict[str, Any] | None:
    """
    Look up an API key by prefix, then verify the full hash.

    Returns user dict (same shape as get_session) if valid, None otherwise.
    Uses admin connection because we don't know the user yet.
    """
    if not bearer_token or not bearer_token.startswith('hbk_'):
        return None

    prefix = bearer_token[:12]
    token_hash = _hash_api_key(bearer_token)

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        # Look up by prefix + hash, join users for active check
        cur.execute("""
            SELECT t.tenant_id, t.id as key_id, t.user_id, t.token_hash,
                   u.email, u.display_name, u.is_active, u.is_developer, u.unit_system,
                   u.home_timezone
            FROM api_tokens t
            JOIN users u ON t.tenant_id = u.tenant_id AND t.user_id = u.id
            WHERE t.key_prefix = %s
              AND t.token_hash = %s
              AND t.revoked_at IS NULL
              AND (t.expires_at IS NULL OR t.expires_at > NOW())
        """, (prefix, token_hash))

        row = cur.fetchone()
        if not row:
            return None

        if not row['is_active']:
            return None

        # Update last_used_at (fire-and-forget, don't fail auth on update error)
        try:
            cur.execute("""
                UPDATE api_tokens SET last_used_at = NOW()
                WHERE tenant_id = %s AND id = %s
            """, (row['tenant_id'], row['key_id']))
            conn.commit()
        except Exception:
            pass

        return {
            'tenant_id': row['tenant_id'],
            'session_id': None,
            'api_key_id': str(row['key_id']),
            'user_id': row['user_id'],
            'username': row['email'],
            'email': row['email'],
            'display_name': row['display_name'],
            'home_timezone': row['home_timezone'],
            'unit_system': row['unit_system'] or 'imperial',
            'is_developer': row.get('is_developer', False),
            'database_name': 'healthv10'
        }

    finally:
        conn.close()


def list_api_keys(user_id: str, tenant_id: int | None = None) -> list[dict[str, Any]]:
    """
    List active API keys for a user (metadata only, never hashes).

    Returns list of dicts with id, key_prefix, device_name, created_at, last_used_at.
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, key_prefix, device_name, token_type,
                   created_at, last_used_at
            FROM api_tokens
            WHERE tenant_id = %s AND user_id = %s AND revoked_at IS NULL
            ORDER BY created_at DESC
        """, (tenant_id, user_id))

        return [dict(row) for row in cur.fetchall()]

    finally:
        conn.close()


def revoke_api_key(key_id: str, user_id: str, tenant_id: int | None = None) -> tuple[bool, str | None]:
    """
    Revoke an API key (soft delete — sets revoked_at).

    Returns (success, error_message).
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE api_tokens
            SET revoked_at = NOW()
            WHERE tenant_id = %s AND id = %s AND user_id = %s AND revoked_at IS NULL
        """, (tenant_id, key_id, user_id))

        if cur.rowcount == 0:
            conn.commit()
            return False, "API key not found or already revoked"

        conn.commit()
        return True, None

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()
