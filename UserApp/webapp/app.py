from flask import Flask, request, jsonify, send_from_directory, g, session, redirect, url_for
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS
import os
from dotenv import load_dotenv
from datetime import datetime
import pytz
import uuid
import json
import logging
from decimal import Decimal
import ipaddress

# Import our auth and db modules
import auth
import db_driver
import db_manager

# Import new three-tier logging system
from logging_config import configure_logging, is_level, BASIC, STANDARD, DEBUG, get_log_level
from logging_middleware import setup_request_logging
from utils import require_auth, table_has_column, local_to_utc, utc_to_local
from unit_conversion import normalize_metric_unit
import analytics

load_dotenv()

class HealthBuddyJSONProvider(DefaultJSONProvider):
    """Extend Flask's default JSON provider to handle UUID, Decimal, datetime."""

    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

app = Flask(__name__, static_folder='.', template_folder='templates')
app.json_provider_class = HealthBuddyJSONProvider
app.json = HealthBuddyJSONProvider(app)

# ==================== STARTUP VALIDATION ====================
# Refuse to start with insecure defaults — .env must be present
_secret_key = os.getenv('SECRET_KEY', '')
if not _secret_key or _secret_key == 'dev-secret-key-change-in-production':
    if os.getenv('FLASK_ENV') != 'testing':
        raise RuntimeError(
            "SECRET_KEY is missing or still set to the default placeholder. "
            "Run ./setup.sh to generate .env or set SECRET_KEY explicitly."
        )
    _secret_key = 'test-secret-key-for-ci'
app.secret_key = _secret_key

# SecurityHardening.md Track 6 — refuse to start when env-var combinations are
# dangerous in ways the runtime code can't distinguish from healthy
# (silent fallbacks, permissive defaults). Currently catches F3
# (HEALTHKIT_SYNC_TOKEN without HEALTHKIT_SYNC_USERNAME); see
# validate_env.py for the full rule set.
from validate_env import assert_env_valid
assert_env_valid()
# Reject request bodies larger than 500 MB at the WSGI layer.
# Without this, an attacker can slowly stream a huge body to tie up a worker slot.
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
# Note: Log level is configured by configure_logging() based on FLASK_LOG_LEVEL env var
# Enable CORS for the LAN-hosted patient web SPA.
# Origins come from CORS_ORIGINS env (dev default: localhost + Mac/RN dev only).
# Deployed envs override with the SPA's LAN origin.
# supports_credentials=True is kept: the SPA fetches with `credentials: 'include'`,
# so the server must respond with Access-Control-Allow-Credentials: true even
# though SPA auth is Bearer-in-JS (the cookies travelling are inert for auth).
# allow_headers must include Authorization so the preflight permits the Bearer.
# See web-spa-backend-contract.md §0.5 + §1.
allowed_origins = os.getenv('CORS_ORIGINS', 'http://localhost:80,http://127.0.0.1:80,http://10.0.2.2:80').split(',')
CORS(
    app,
    origins=allowed_origins,
    supports_credentials=True,
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    max_age=600,
)

# ==================== BLUEPRINT REGISTRATION ====================
# Route blueprints for API endpoints (split from monolithic app.py)
# NOTE: Auth routes remain in this file due to template rendering complexity
from routes import register_blueprints
register_blueprints(app)

# Single source of truth for auth decorator — used by both app.py routes and blueprints
from utils import require_auth

PORT = int(os.getenv('PORT', 5000))
THEME = os.getenv('THEME', 'default')

# ==================== THREE-TIER LOGGING ====================
# Set FLASK_LOG_LEVEL=BASIC|STANDARD|DEBUG in environment
# - BASIC: One line per request (production)
# - STANDARD: + Query timing, row counts (monitoring)
# - DEBUG: + Full SQL, headers, bodies (troubleshooting)
# Legacy VERBOSE_LOGGING still supported for backwards compatibility
VERBOSE = os.getenv('VERBOSE_LOGGING', 'false').lower() in ('true', '1', 'yes')
if VERBOSE and os.getenv('FLASK_LOG_LEVEL') is None:
    os.environ['FLASK_LOG_LEVEL'] = 'DEBUG'  # Map legacy VERBOSE to new DEBUG level

# Configure three-tier logging system
configure_logging(app)

# Set up request/response logging middleware
setup_request_logging(app)

app.logger.info("Logging configured: level=%s", get_log_level())

def vlog(msg, *args, **kwargs):
    """Log only at DEBUG level. Use for high-frequency debug info."""
    if is_level(DEBUG):
        app.logger.debug(msg, *args, **kwargs)


# Note: Request/response logging is now handled by logging_middleware.py
# The setup_request_logging(app) call above registers the handlers

# Web UI Access Control
# Source-IP allowlist for EVERY route (API included). Home Edition is a
# household-LAN / Tailscale appliance — it must never answer a public peer at the
# application layer, regardless of bind address or bearer token. We enumerate the
# trusted networks explicitly rather than use ipaddress.is_private, whose
# membership (notably CGNAT 100.64/10) varies across Python versions and is
# broader than RFC1918.
_TRUSTED_SOURCE_NETS = tuple(ipaddress.ip_network(c) for c in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC1918 (LAN)
    "100.64.0.0/10",                                    # CGNAT / RFC6598 — Tailscale et al.
    "127.0.0.0/8",                                      # IPv4 loopback (local self-probes)
    "::1/128",                                          # IPv6 loopback
    "fc00::/7",                                         # IPv6 ULA (incl. Tailscale v6)
))


def _is_trusted_source(addr) -> bool:
    """True if addr is in the LAN/CGNAT/loopback allowlist.

    Uses the real peer address (request.remote_addr); X-Forwarded-For is
    intentionally NOT consulted — Home Edition has no reverse proxy, so a
    forwarded header would be attacker-spoofable. IPv4-mapped IPv6 peers
    (::ffff:a.b.c.d) are unwrapped so the real address is what gets matched.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except (TypeError, ValueError):
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _TRUSTED_SOURCE_NETS)


@app.before_request
def source_ip_filter():
    """Refuse any request whose source IP is outside the household LAN, CGNAT
    (Tailscale), or loopback — applied to every route, API included.

    Defense-in-depth beneath the BIND_ADDR bind knob: even if the box is bound
    to 0.0.0.0 or fronted by a tunnel, it never serves a public peer. Returns
    403 Forbidden for anything off-allowlist.
    """
    if _is_trusted_source(request.remote_addr):
        return None
    app.logger.warning(f"Blocked non-LAN source {request.remote_addr} to {request.path}")
    return "Forbidden", 403


# ==================== AUTHENTICATION ====================

 

def get_user_db_connection():
    """
    Get database connection for the current logged-in user.

    Home Edition: no RLS — queries must scope by tenant_id and user_id
    at the application level.
    """
    vlog("get_user_db_connection: g.user=%s", g.user)

    user_id = g.user.get('user_id')
    tenant_raw = g.user.get('tenant_id', 1)
    try:
        tenant_id = int(tenant_raw) if tenant_raw not in (None, '') else 1
    except (TypeError, ValueError):
        tenant_id = 1

    if not user_id:
        app.logger.error("get_user_db_connection: No user_id available in g.user")
        raise ValueError("No user_id available for current user context")

    # Keep request context normalized so downstream handlers never see blank tenant.
    g.user['tenant_id'] = tenant_id

    conn = db_manager.get_direct_connection_for_user(user_id, tenant_id)
    vlog("get_user_db_connection: connected for tenant_id=%s, user_id=%s", tenant_id, user_id)
    return conn


def get_user_id():
    """
    Get the current user's ID from the session context.

    We always use the master user_id from the session. No need to look
    up a "local" user_id since all data is in one database.
    """
    user_id = g.user.get('user_id')
    if not user_id:
        raise ValueError("No user_id available in session context")
    return user_id

# ==================== AUTH ROUTES ====================


@app.route('/login', methods=['GET'])
def login():
    """Show login page"""
    from flask import session, redirect, url_for, render_template
    session_id = session.get('session_id')
    if session_id and auth.get_session(session_id):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def do_login():
    """Process login - accepts email or username"""
    from flask import session, redirect, url_for, render_template
    import uuid as uuid_module
    data = request.get_json() if request.is_json else request.form
    # Accept 'email', 'username', or 'identifier' for backwards compatibility
    identifier = (data.get('email') or data.get('username') or data.get('identifier', '')).strip().lower()
    password = data.get('password', '')

    if not identifier or not password:
        if request.is_json:
            return jsonify({'error': 'Email and password required'}), 400
        return render_template('login.html', error='Email and password required')

    user = auth.authenticate_user(identifier, password)
    if not user:
        analytics.capture('login_attempted', {'outcome': 'failed'})
        if request.is_json:
            return jsonify({'error': 'Invalid email or password'}), 401
        return render_template('login.html', error='Invalid email or password')

    # Check if 2FA is enabled for this user
    requires_2fa, user_id, _ = auth.check_2fa_required(identifier)

    if requires_2fa:
        analytics.capture('login_attempted', {'outcome': '2fa_required', 'tenant_id': user.get('tenant_id')})
        # Create a pending 2FA session
        pending_token = str(uuid_module.uuid4())
        tenant_id = user.get('tenant_id', 1)

        conn = db_manager.get_direct_admin_connection()
        cur = conn.cursor()
        try:
            from datetime import datetime, timezone, timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            cur.execute("""
                INSERT INTO sessions (tenant_id, session_id, user_id, created_at, expires_at, ip_address, user_agent)
                VALUES (%s, %s, %s, NOW(), %s, %s, '2FA_PENDING')
            """, (tenant_id, pending_token, user_id, expires_at, request.remote_addr))
            conn.commit()
        finally:
            conn.close()

        if request.is_json:
            return jsonify({
                'success': False,
                'requires_2fa': True,
                'pending_2fa_token': pending_token,
                'message': 'Please enter your 2FA code'
            })
        # For web UI, redirect to 2FA verification page
        return render_template('login.html', requires_2fa=True, pending_token=pending_token, email=identifier)

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent')
    session_id = auth.create_session(user['id'], ip_address, user_agent)

    session['session_id'] = session_id
    session['username'] = user.get('username') or user.get('email')

    analytics.capture('login_attempted', {'outcome': 'success', 'tenant_id': user.get('tenant_id')})
    analytics.identify(user)

    if request.is_json:
        return jsonify({
            'success': True,
            'email': user.get('email'),
            'display_name': user.get('display_name'),
            'username': user.get('username'),  # May be None for new users
            'token': session_id
        })
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    """Logout user"""
    from flask import session, redirect, url_for
    session_id = session.get('session_id')
    if session_id:
        auth.delete_session(session_id)
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/v1/logout', methods=['GET'])
@app.route('/api/v2/logout', methods=['GET'])
@require_auth
def api_logout():
    """API logout endpoint - clears session token."""
    from flask import session as flask_session
    session_id = g.user.get('session_id') if hasattr(g, 'user') else None
    if not session_id:
        session_id = flask_session.get('session_id')
    if session_id:
        auth.delete_session(session_id)
    flask_session.clear()
    return jsonify({'success': True})

@app.route('/api/v1/login', methods=['POST'])
@app.route('/api/v2/login', methods=['POST'])
def api_login():
    """API login endpoint - accessible from external IPs. Accepts email or username."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    # Accept 'email', 'username', or 'identifier' for backwards compatibility
    identifier = (data.get('email') or data.get('username') or data.get('identifier', '')).strip().lower()
    password = data.get('password', '')

    if not identifier or not password:
        return jsonify({'error': 'Email and password required'}), 400

    user = auth.authenticate_user(identifier, password)
    if not user:
        return jsonify({'error': 'Invalid email or password'}), 401

    # Check if 2FA is enabled for this user
    requires_2fa, user_id, _ = auth.check_2fa_required(identifier)

    if requires_2fa:
        # Create a pending 2FA session (not a real session yet)
        # Use '2FA_PENDING' as user_agent marker to identify pending sessions
        import uuid
        pending_token = str(uuid.uuid4())
        tenant_id = user.get('tenant_id', 1)

        conn = db_manager.get_direct_admin_connection()
        cur = conn.cursor()
        try:
            from datetime import datetime, timezone, timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            cur.execute("""
                INSERT INTO sessions (tenant_id, session_id, user_id, created_at, expires_at, ip_address, user_agent)
                VALUES (%s, %s, %s, NOW(), %s, %s, '2FA_PENDING')
            """, (tenant_id, pending_token, user_id, expires_at, request.remote_addr))
            conn.commit()
        finally:
            conn.close()

        return jsonify({
            'success': False,
            'requires_2fa': True,
            'pending_2fa_token': pending_token,
            'message': 'Please enter your 2FA code'
        })

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent')
    tenant_id = user.get('tenant_id', 1)
    session_id = auth.create_session(user['id'], ip_address, user_agent, tenant_id)

    return jsonify({
        'success': True,
        'user_id': str(user['id']),
        'email': user.get('email'),
        'display_name': user.get('display_name'),
        'username': user.get('username'),  # May be None for new email-based users
        'token': session_id,
        'tenant_id': tenant_id,
        'database': user.get('database_name'),
        'created_at': user.get('created_at'),
    })

@app.route('/api/v1/session')
@app.route('/api/v2/session')
@require_auth
def get_session_info():
    """Get current session info"""
    return jsonify({
        'id': g.user.get('user_id'),
        'user_id': g.user.get('user_id'),
        'email': g.user.get('email') or g.user.get('username'),
        'username': g.user['username'],
        'tenant_id': g.user.get('tenant_id', 1),
        'database': g.user['database_name'],
        'created_at': g.user.get('created_at'),
        'is_developer': g.user.get('is_developer', False),
        'unit_system': g.user.get('unit_system', 'imperial'),
        'home_timezone': g.user.get('home_timezone'),
    })


@app.route('/api/v1/is-developer')
@app.route('/api/v2/is-developer')
@require_auth
def is_developer():
    """Check whether the authenticated user has the developer flag."""
    return jsonify({'is_developer': g.user.get('is_developer', False)})


@app.route('/api/v1/me/uuid')
@app.route('/api/v2/me/uuid')
@require_auth
def get_my_uuid():
    """Return the authenticated user's UUID.

    Lightweight endpoint for mobile clients to verify their locally-stored
    UUID matches the server-assigned value.
    """
    return jsonify({'user_id': g.user.get('user_id')})


@app.route('/api/v1/change-password', methods=['POST'])
@app.route('/api/v2/change-password', methods=['POST'])
@require_auth
def api_change_password():
    """Change password for authenticated user (API v1)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    current_password = data.get('current_password') or data.get('old_password') or ''
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({'error': 'current_password/old_password and new_password required'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    success, error = auth.change_password(g.user['user_id'], current_password, new_password)

    if success:
        return jsonify({'success': True})

    error_message = error.get('message') if isinstance(error, dict) else error
    error_code = error.get('code') if isinstance(error, dict) else None

    # Prefer explicit codes and exact known messages over fragile substring matching.
    if error_code in {'current_password_incorrect', 'invalid_current_password'}:
        return jsonify({'error': error_message}), 401
    if error_code == 'user_not_found':
        return jsonify({'error': error_message}), 404
    if error_message in {'Current password is incorrect'}:
        return jsonify({'error': error_message}), 401
    if error_message in {'User not found'}:
        return jsonify({'error': error_message}), 404
    return jsonify({'error': error_message}), 400


@app.route('/api/v1/settings/unit-system', methods=['PATCH'])
@app.route('/api/v2/settings/unit-system', methods=['PATCH'])
@require_auth
def api_set_unit_system():
    """Set the user's display unit system (imperial or metric)."""
    data = request.get_json(silent=True) or {}
    unit_system = data.get('unit_system')
    if unit_system not in ('imperial', 'metric'):
        return jsonify({'error': "unit_system must be 'imperial' or 'metric'"}), 400

    if not auth.set_unit_system(g.user['user_id'], unit_system, g.user.get('tenant_id', 1)):
        return jsonify({'error': 'User not found'}), 404

    return jsonify({'unit_system': unit_system})


@app.route('/api/v1/mcp-config', methods=['GET'])
@app.route('/api/v2/mcp-config', methods=['GET'])
@require_auth
def api_mcp_config():
    """
    Get MCP (Model Context Protocol) configuration for healthv10.

    In v10, MCP access is through the API (mcp_server.py), not direct DB.
    This endpoint returns API-based MCP configuration.
    """
    user_id = g.user['user_id']
    mcp_base = os.getenv('MCP_BASE_URL', 'http://localhost:13282')

    return jsonify({
        'database_name': 'healthv10',
        'mcp_user': g.user.get('email', g.user.get('username', 'unknown')),
        'host': mcp_base.split('://')[1].split(':')[0] if '://' in mcp_base else 'localhost',
        'port': mcp_base.rsplit(':', 1)[1] if mcp_base.count(':') > 1 else '443',
        'connection_string': f"{mcp_base}/sse",
        'claude_desktop_config': {
            'minowa': {
                'command': 'npx',
                'args': [
                    '-y', 'supergateway',
                    '--sse', f'{mcp_base}/sse',
                    '--header', 'authorization:Bearer YOUR_API_KEY'
                ]
            }
        },
        'note': 'Replace YOUR_API_KEY with an API key generated below.'
    })


# ============================================================================
# API Key Management Endpoints
# ============================================================================

@app.route('/api/v1/api-keys', methods=['POST'])
@app.route('/api/v2/api-keys', methods=['POST'])
@require_auth
def api_create_api_key():
    """
    Create a new long-lived API key for MCP or integration use.

    The raw key is returned ONCE in this response and never stored.
    Users must copy it immediately.

    Body (optional): { "label": "My MCP key" }
    Returns: { "id": "...", "key": "hbk_...", "label": "...", "key_prefix": "...", "created_at": "..." }
    """
    data = request.get_json(silent=True) or {}
    label = data.get('label', 'MCP')
    if not isinstance(label, str) or len(label.strip()) == 0:
        label = 'MCP'
    label = label.strip()[:100]  # Cap label length

    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)
    ip_address = request.remote_addr

    key_id, raw_key = auth.create_api_key(user_id, tenant_id, label, ip_address)
    if key_id is None:
        # raw_key contains the error message (e.g. per-user key cap reached)
        return jsonify({'error': raw_key}), 409

    return jsonify({
        'id': str(key_id),
        'key': raw_key,
        'label': label,
        'key_prefix': raw_key[:12],
        'created_at': datetime.now(pytz.utc).isoformat(),
    }), 201


@app.route('/api/v1/api-keys', methods=['GET'])
@app.route('/api/v2/api-keys', methods=['GET'])
@require_auth
def api_list_api_keys():
    """
    List all active API keys for the current user.

    Never returns the full key or hash — only prefix, label, and timestamps.
    Returns: [{ "id": "...", "key_prefix": "...", "label": "...", "created_at": "...", "last_used_at": "..." }]
    """
    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)

    keys = auth.list_api_keys(user_id, tenant_id)
    return jsonify(keys)


@app.route('/api/v1/api-keys/<key_id>', methods=['DELETE'])
@app.route('/api/v2/api-keys/<key_id>', methods=['DELETE'])
@require_auth
def api_revoke_api_key(key_id):
    """
    Revoke an API key (soft delete — sets revoked_at).

    Only the key owner can revoke their own keys.
    Returns: { "success": true }
    """
    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)

    success, error = auth.revoke_api_key(key_id, user_id, tenant_id)
    if not success:
        return jsonify({'error': error or 'Key not found or already revoked'}), 404

    return jsonify({'success': True})


# ============================================================================
# Two-Factor Authentication (2FA) Endpoints
# ============================================================================

@app.route('/api/v1/2fa/status', methods=['GET'])
@app.route('/api/v2/2fa/status', methods=['GET'])
@require_auth
def api_2fa_status():
    """Get current 2FA status for the authenticated user"""
    user_id = g.user['user_id']
    status = auth.get_user_2fa_status(user_id)

    # Add backup codes count if 2FA is enabled
    if status['enabled']:
        status['backup_codes_remaining'] = auth.get_remaining_backup_codes_count(user_id)

    return jsonify(status)


@app.route('/api/v1/2fa/setup', methods=['POST'])
@app.route('/api/v2/2fa/setup', methods=['POST'])
@require_auth
def api_2fa_setup():
    """
    Start 2FA setup - generates secret and returns QR code URI.
    User must verify with a code before 2FA is actually enabled.
    """
    user_id = g.user['user_id']
    secret, uri, error = auth.setup_2fa(user_id)

    if error:
        return jsonify({'error': error}), 400

    return jsonify({
        'secret': secret,
        'uri': uri,
        'message': 'Scan the QR code with your authenticator app, then verify with a code'
    })


@app.route('/api/v1/2fa/verify-setup', methods=['POST'])
@app.route('/api/v2/2fa/verify-setup', methods=['POST'])
@require_auth
def api_2fa_verify_setup():
    """
    Verify setup code and enable 2FA.
    Returns backup codes that user should save.
    """
    user_id = g.user['user_id']
    data = request.get_json()

    if not data or not data.get('code'):
        return jsonify({'error': 'Verification code required'}), 400

    code = str(data['code']).strip()

    backup_codes, error = auth.verify_and_enable_2fa(user_id, code)

    if error:
        return jsonify({'error': error}), 400

    return jsonify({
        'success': True,
        'message': '2FA enabled successfully. Save these backup codes in a safe place.',
        'backup_codes': backup_codes
    })


@app.route('/api/v1/2fa/disable', methods=['POST'])
@app.route('/api/v2/2fa/disable', methods=['POST'])
@require_auth
def api_2fa_disable():
    """
    Disable 2FA (requires password confirmation).
    """
    user_id = g.user['user_id']
    data = request.get_json()

    if not data or not data.get('password'):
        return jsonify({'error': 'Password required'}), 400

    success, error = auth.disable_2fa(user_id, data['password'])

    if not success:
        return jsonify({'error': error}), 400

    return jsonify({
        'success': True,
        'message': '2FA has been disabled'
    })


@app.route('/api/v1/2fa/regenerate-backup-codes', methods=['POST'])
@app.route('/api/v2/2fa/regenerate-backup-codes', methods=['POST'])
@require_auth
def api_2fa_regenerate_backup_codes():
    """
    Generate new backup codes (invalidates old ones, requires password).
    """
    user_id = g.user['user_id']
    data = request.get_json()

    if not data or not data.get('password'):
        return jsonify({'error': 'Password required'}), 400

    codes, error = auth.regenerate_backup_codes(user_id, data['password'])

    if error:
        return jsonify({'error': error}), 400

    return jsonify({
        'success': True,
        'message': 'New backup codes generated. Previous codes are now invalid.',
        'backup_codes': codes
    })


@app.route('/api/v1/2fa/verify', methods=['POST'])
@app.route('/api/v2/2fa/verify', methods=['POST'])
def api_2fa_verify():
    """
    Verify 2FA code during login flow.
    Called after password verification returns requires_2fa=true.
    Expects: pending_2fa_token (from login response) and code (TOTP or backup code).
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    pending_token = data.get('pending_2fa_token', '').strip()
    code = str(data.get('code', '')).strip()

    if not pending_token or not code:
        return jsonify({'error': 'pending_2fa_token and code required'}), 400

    # Validate the pending token and get user_id
    # Pending tokens are stored in sessions table with a special marker
    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT user_id, created_at
            FROM sessions
            WHERE session_id = %s AND user_agent = '2FA_PENDING'
        """, (pending_token,))
        pending = cur.fetchone()

        if not pending:
            return jsonify({'error': 'Invalid or expired 2FA token'}), 401

        # Check if token is too old (5 minute limit for 2FA verification)
        from datetime import datetime, timezone, timedelta
        if pending['created_at'] < datetime.now(timezone.utc) - timedelta(minutes=5):
            # Delete expired pending token
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (pending_token,))
            conn.commit()
            return jsonify({'error': '2FA verification expired, please login again'}), 401

        user_id = pending['user_id']

    finally:
        conn.close()

    # Verify the 2FA code
    success, is_backup, error = auth.verify_2fa_login(user_id, code)

    if not success:
        analytics.capture('2fa_verified', {'outcome': 'failed'})
        return jsonify({'error': error or 'Invalid 2FA code'}), 401

    analytics.capture('2fa_verified', {'outcome': 'success'})

    # Delete the pending token
    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM sessions WHERE session_id = %s", (pending_token,))
        conn.commit()

        # Get user info for session creation
        cur.execute("""
            SELECT tenant_id, email, display_name
            FROM users WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()
    finally:
        conn.close()

    if not user:
        return jsonify({'error': 'User not found'}), 401

    # Create actual session
    new_session_id = auth.create_session(
        user_id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent')
    )

    # Also set Flask session for web users
    from flask import session as flask_session
    flask_session['session_id'] = new_session_id
    flask_session['username'] = user.get('email')

    response_data = {
        'success': True,
        'user_id': str(user_id),
        'email': user['email'],
        'display_name': user['display_name'],
        'token': new_session_id,
        'tenant_id': user['tenant_id'],
        'database': 'healthv10'
    }

    if is_backup:
        remaining = auth.get_remaining_backup_codes_count(user_id)
        response_data['backup_code_used'] = True
        response_data['backup_codes_remaining'] = remaining
        if remaining <= 2:
            response_data['warning'] = f'Only {remaining} backup codes remaining. Consider regenerating them.'

    return jsonify(response_data)


# Serve the main HTML page
@app.route('/')
@require_auth
def index():
    """Serve the main application page"""
    return send_from_directory('.', 'index.html')

# Serve theme configuration
@app.route('/api/v1/config')
@app.route('/api/v2/config')
@require_auth
def get_config():
    """Serve application configuration"""
    return jsonify({'theme': THEME})

# Serve static CSS and JS files (require auth)
@app.route('/<path:filename>')
@require_auth
def serve_static(filename):
    """Serve static files from current directory"""
    # Only serve CSS, JS, and other safe static files
    allowed_extensions = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf'}
    if any(filename.endswith(ext) for ext in allowed_extensions):
        return send_from_directory('.', filename)
    # Redirect everything else to index
    return send_from_directory('.', 'index.html')



def get_first_user_record():
    """
    Return the first active user's record (for token auth fallback).
    In v10, there's no database_name - we use the shared healthv10 database.
    """
    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, email, display_name
            FROM users
            WHERE is_active = true
            ORDER BY id
            LIMIT 1
            """
        )
        user = cur.fetchone()
        if user:
            # Add backwards-compatible fields
            user['username'] = user.get('email')
            user['database_name'] = 'healthv10'
        return user
    finally:
        cur.close()
        conn.close()


# ==================== HEALTH SYNC (HealthKit) ====================

HEALTH_SYNC_TYPES = {
    'steps',
    'heart_rate',
    'resting_heart_rate',
    'sleep',
    'nutrition',
    'active_energy_burned',
    'basal_energy_burned',
    'distance_walking_running',
    'workout',
    'workout_route',
    'floors_climbed',
    'wheelchair_pushes',
    'hydration',
    'heart_rate_variability',
    'respiratory_rate',
    'body_temperature',
    'basal_body_temperature',
    'medication',
    'weight',
    'height',
    'body_fat_percentage',
    'lean_body_mass',
    'blood_glucose',
    'oxygen_saturation',
    'vo2_max',
    'allergy_record',
    'condition_record',
    'immunization_record',
    'lab_result_record',
    'medication_record',
    'procedure_record',
    'vital_sign_record',
    'apple_exercise_time',
    'apple_stand_hour',
    'mindful_session',
}
CLINICAL_SYNC_TYPES = {
    'allergy_record',
    'condition_record',
    'immunization_record',
    'lab_result_record',
    'medication_record',
    'procedure_record',
    'vital_sign_record',
}
BLOOD_PRESSURE_SYNC_TYPE = 'blood_pressure'
# Keep this aligned with DB health_metrics_type_check.
# Includes raw sync types plus canonical aliases used by server-side normalization.
HEALTH_METRICS_ALLOWED_TYPES = set(HEALTH_SYNC_TYPES) | {
    'temperature',
    'blood_oxygen',
}
HEALTH_METRICS_TYPE_ALIASES = {
    'resting_heart_rate': 'heart_rate',
    'body_temperature': 'temperature',
    'basal_body_temperature': 'temperature',
    'oxygen_saturation': 'blood_oxygen',
}

WEIGHT_LOINC_CODES = {'29463-7', '3141-9'}
GLUCOSE_LOINC_CODES = {'2339-0', '2345-7', '2349-9', '14771-0'}
TEMPERATURE_LOINC_CODES = {'8310-5', '8331-1'}
HEART_RATE_LOINC_CODES = {'8867-4'}
RESPIRATORY_RATE_LOINC_CODES = {'9279-1'}
OXYGEN_SAT_LOINC_CODES = {'2708-6', '59408-5'}
HEIGHT_LOINC_CODES = {'8302-2'}
SYSTOLIC_LOINC_CODES = {'8480-6'}
DIASTOLIC_LOINC_CODES = {'8462-4'}


def _normalize_unit(unit):
    if not unit:
        return None
    unit_str = str(unit).lower()
    if 'kg' in unit_str:
        return 'kg'
    if 'lb' in unit_str or 'pound' in unit_str:
        return 'lb'
    if 'cel' in unit_str or 'degc' in unit_str or 'celsius' in unit_str:
        return 'degC'
    if 'fahrenheit' in unit_str or 'degf' in unit_str:
        return 'degF'
    return unit


def _get_codings(fhir):
    code = fhir.get('code') if isinstance(fhir.get('code'), dict) else {}
    coding = code.get('coding')
    return coding if isinstance(coding, list) else []


def _get_code_text(fhir):
    code = fhir.get('code') if isinstance(fhir.get('code'), dict) else {}
    text = code.get('text')
    return text if isinstance(text, str) else ''


def _matches_code(codings, code_set):
    for entry in codings:
        if not isinstance(entry, dict):
            continue
        if entry.get('code') in code_set:
            return True
    return False


def _label_matches(label, keywords):
    if not label:
        return False
    lowered = str(label).lower()
    return any(keyword in lowered for keyword in keywords)


def _resolve_type_from_observation(fhir, display_name):
    codings = _get_codings(fhir)
    code_text = _get_code_text(fhir)
    display_texts = [display_name, code_text] + [
        entry.get('display') for entry in codings if isinstance(entry, dict)
    ]
    if _matches_code(codings, WEIGHT_LOINC_CODES) or any(
        _label_matches(text, ['weight']) for text in display_texts
    ):
        return 'weight'
    if _matches_code(codings, GLUCOSE_LOINC_CODES) or any(
        _label_matches(text, ['glucose', 'blood sugar']) for text in display_texts
    ):
        return 'blood_glucose'
    if _matches_code(codings, TEMPERATURE_LOINC_CODES) or any(
        _label_matches(text, ['temperature']) for text in display_texts
    ):
        return 'body_temperature'
    if _matches_code(codings, HEART_RATE_LOINC_CODES) or any(
        _label_matches(text, ['heart rate', 'pulse']) for text in display_texts
    ):
        return 'heart_rate'
    if _matches_code(codings, RESPIRATORY_RATE_LOINC_CODES) or any(
        _label_matches(text, ['respiratory rate']) for text in display_texts
    ):
        return 'respiratory_rate'
    if _matches_code(codings, OXYGEN_SAT_LOINC_CODES) or any(
        _label_matches(text, ['oxygen saturation', 'spo2']) for text in display_texts
    ):
        return 'oxygen_saturation'
    if _matches_code(codings, HEIGHT_LOINC_CODES) or any(
        _label_matches(text, ['height']) for text in display_texts
    ):
        return 'height'
    return None


def _extract_blood_pressure(fhir):
    components = fhir.get('component') if isinstance(fhir.get('component'), list) else []
    systolic = None
    diastolic = None
    unit = None
    for component in components:
        if not isinstance(component, dict):
            continue
        _code = component.get('code')
        code = _code if isinstance(_code, dict) else {}
        _coding = code.get('coding')
        codings = _coding if isinstance(_coding, list) else []
        _vq = component.get('valueQuantity')
        value_qty = _vq if isinstance(_vq, dict) else None
        if not value_qty:
            continue
        try:
            value = float(value_qty.get('value'))
        except (TypeError, ValueError):
            continue
        comp_unit = _normalize_unit(value_qty.get('unit') or value_qty.get('code'))
        if _matches_code(codings, SYSTOLIC_LOINC_CODES):
            systolic = value
            unit = comp_unit or unit
        if _matches_code(codings, DIASTOLIC_LOINC_CODES):
            diastolic = value
            unit = comp_unit or unit
    if systolic is None or diastolic is None:
        return None
    return {'systolic': systolic, 'diastolic': diastolic, 'unit': unit or 'mmHg'}


def _extract_readings_from_record(record):
    if not isinstance(record, dict):
        return []
    display_name = record.get('displayName') or record.get('display_name') or ''
    fhir_raw = record.get('fhir')
    if not fhir_raw:
        return []
    try:
        fhir = json.loads(fhir_raw) if isinstance(fhir_raw, str) else fhir_raw
    except Exception:
        return []
    if not isinstance(fhir, dict) or fhir.get('resourceType') != 'Observation':
        return []

    timestamp = fhir.get('effectiveDateTime') or fhir.get('issued')
    if not timestamp:
        period = fhir.get('effectivePeriod') if isinstance(fhir.get('effectivePeriod'), dict) else None
        if period:
            timestamp = period.get('start')

    bp = _extract_blood_pressure(fhir)
    if bp:
        return [{
            'type': 'blood_pressure',
            'systolic': bp['systolic'],
            'diastolic': bp['diastolic'],
            'unit': bp['unit'],
            'timestamp': timestamp,
            'display_name': display_name or None,
        }]

    value_qty = fhir.get('valueQuantity') if isinstance(fhir.get('valueQuantity'), dict) else None
    if not value_qty:
        return []
    try:
        value = float(value_qty.get('value'))
    except (TypeError, ValueError):
        return []
    unit = _normalize_unit(value_qty.get('unit') or value_qty.get('code'))
    resolved_type = _resolve_type_from_observation(fhir, display_name)
    if not resolved_type:
        return []

    return [{
        'type': resolved_type,
        'value': value,
        'unit': unit,
        'timestamp': timestamp,
        'display_name': display_name or None,
    }]

def has_health_metrics_sync_dedupe_index(conn):
    """Return True when the optional sync dedupe index exists."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'health_metrics'
              AND indexname = 'idx_health_metrics_sync_dedupe'
            LIMIT 1
            """
        )
        return cur.fetchone() is not None
    finally:
        cur.close()

def has_bp_sync_dedupe_index(conn):
    """Return True when the optional BP sync dedupe index exists."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'health_blood_pressure_readings'
              AND indexname = 'idx_bp_sync_dedupe'
            LIMIT 1
            """
        )
        return cur.fetchone() is not None
    finally:
        cur.close()

# Map v2 HealthKit type identifiers to the legacy `health_metrics.metric_type`
# enum so the v2 sync path can continue populating the table the mobile UI
# currently reads from.
#
# TODO(projection): delete this table when the hkit_* → health_* projector
# lands (Phase C of the HealthKit Consistency Plan). At that point health_*
# will be populated by the projector, not by direct translation inside the
# request handler.
HKIT_TO_LEGACY_METRIC_TYPE = {
    'HKQuantityTypeIdentifierStepCount': 'steps',
    'HKQuantityTypeIdentifierHeartRate': 'heart_rate',
    'HKQuantityTypeIdentifierRestingHeartRate': 'resting_heart_rate',
    'HKQuantityTypeIdentifierActiveEnergyBurned': 'active_energy_burned',
    'HKQuantityTypeIdentifierBasalEnergyBurned': 'basal_energy_burned',
    'HKQuantityTypeIdentifierDistanceWalkingRunning': 'distance_walking_running',
    'HKQuantityTypeIdentifierFlightsClimbed': 'floors_climbed',
    'HKQuantityTypeIdentifierPushCount': 'wheelchair_pushes',
    'HKQuantityTypeIdentifierDietaryWater': 'hydration',
    'HKQuantityTypeIdentifierHeartRateVariabilitySDNN': 'heart_rate_variability',
    'HKQuantityTypeIdentifierRespiratoryRate': 'respiratory_rate',
    'HKQuantityTypeIdentifierBodyTemperature': 'body_temperature',
    'HKQuantityTypeIdentifierBasalBodyTemperature': 'basal_body_temperature',
    'HKQuantityTypeIdentifierBodyMass': 'weight',
    'HKQuantityTypeIdentifierHeight': 'height',
    'HKQuantityTypeIdentifierBodyFatPercentage': 'body_fat_percentage',
    'HKQuantityTypeIdentifierLeanBodyMass': 'lean_body_mass',
    'HKQuantityTypeIdentifierBloodGlucose': 'blood_glucose',
    'HKQuantityTypeIdentifierOxygenSaturation': 'oxygen_saturation',
    'HKQuantityTypeIdentifierVO2Max': 'vo2_max',
    'HKCategoryTypeIdentifierSleepAnalysis': 'sleep',
}


def _format_source_string(source_info):
    """Collapse a v2 source descriptor into the legacy `source` string.

    The legacy `health_metrics.source` column is a single free-form text
    field. v2 carries the full provenance (bundle_id, device_name,
    device_model, version). For the transition write we flatten back to
    something close to what v1 clients would have sent — the device name
    if available, else the source name — so the legacy dedupe key keeps
    behaving consistently.
    """
    if not isinstance(source_info, dict):
        return None
    return (
        source_info.get('device_name')
        or source_info.get('source_name')
        or source_info.get('source_bundle_id')
    )


def _sync_healthkit_v2(payload):
    """Handle a payload_version=2 HealthKit sync.

    See ``sync_healthkit`` for overall semantics. This handler:

    1. Opens a fresh connection for the current user.
    2. Calls ``healthkit_writer.write_v2_payload`` to land the canonical
       data in the ``hkit_*`` tables.
    3. **Additionally** translates the same v2 samples back into the
       legacy ``health_metrics`` / ``health_blood_pressure_readings``
       row shapes and inserts them, so the mobile UI continues to
       function during the transition. This legacy dual-write is
       temporary — see the TODO(projection) marker on
       ``HKIT_TO_LEGACY_METRIC_TYPE`` above.
    4. Commits. On any exception, rolls back and returns 500 so the
       client retries (all writes are idempotent under their dedupe
       indexes).
    """
    from healthkit_writer import WriterContext, write_v2_payload, get_sync_anchors

    conn = get_user_db_connection()
    db_user_id = get_user_id()
    if not db_user_id:
        conn.close()
        return jsonify({'error': 'No valid user found for sync'}), 500

    tenant_raw = g.user.get('tenant_id', 1)
    try:
        tenant_id = int(tenant_raw) if tenant_raw not in (None, '') else 1
    except (TypeError, ValueError):
        tenant_id = 1

    cur = conn.cursor()

    try:
        ctx = WriterContext(tenant_id=tenant_id, user_id=str(db_user_id))
        counts, bp_summaries = write_v2_payload(cur, ctx, payload)
    except ValueError as ve:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': str(ve)}), 400
    except Exception:
        app.logger.exception("healthkit sync v2: failed to write hkit_* tables")
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Failed to store HealthKit canonical data'}), 500

    # Legacy dual-write for mobile UI continuity.
    # TODO(projection): remove this block when the hkit_* → health_*
    # projector lands in Phase C. For now the v2 path populates both
    # tables so a client that cuts over to v2 does not blank the UI.
    sources_list = payload.get('sources') or []
    legacy_rows = []
    legacy_bp_rows = []
    now_utc = datetime.now(pytz.utc)

    for sample in payload.get('samples') or []:
        if not isinstance(sample, dict):
            continue
        type_identifier = sample.get('type_identifier')
        if type_identifier == 'HKCorrelationTypeIdentifierBloodPressure':
            continue  # handled via bp_summaries below
        metric_type = HKIT_TO_LEGACY_METRIC_TYPE.get(type_identifier)
        if metric_type is None:
            continue
        try:
            value = float(sample.get('value'))
        except (TypeError, ValueError):
            continue
        start = sample.get('start')
        try:
            recorded_at = local_to_utc(start)
        except Exception:
            continue
        src_ref = sample.get('source_ref')
        src_info = sources_list[src_ref] if (
            isinstance(src_ref, int) and 0 <= src_ref < len(sources_list)
        ) else None
        source_string = _format_source_string(src_info)
        metadata = sample.get('metadata') if isinstance(sample.get('metadata'), dict) else None
        notes_payload = {'metadata': metadata} if metadata else None
        legacy_rows.append((
            tenant_id,
            uuid.uuid4(),
            db_user_id,
            recorded_at,
            metric_type,
            value,
            # Normalize HealthKit unit spellings ('lb', 'degF') to the
            # unit_conversion vocabulary; unknown units stay raw.
            normalize_metric_unit(metric_type, sample.get('unit')) or sample.get('unit'),
            source_string,
            json.dumps(notes_payload) if notes_payload else None,
            now_utc,
        ))

    for bp in bp_summaries:
        legacy_bp_rows.append((
            tenant_id,
            uuid.uuid4(),
            db_user_id,
            bp['start'],
            bp['systolic'],
            bp['diastolic'],
            None,  # pulse — not carried through the v2 BP shape
            now_utc,
        ))

    inserted_legacy = 0
    inserted_legacy_bp = 0

    try:
        if legacy_rows:
            if has_health_metrics_sync_dedupe_index(conn):
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_metrics
                        (tenant_id, id, user_id, recorded_at, metric_type,
                         value, unit, source, notes, created_at)
                    VALUES %s
                    ON CONFLICT (tenant_id, user_id, metric_type, recorded_at, value, unit, source)
                    DO NOTHING
                    """,
                    legacy_rows,
                )
            else:
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_metrics
                        (tenant_id, id, user_id, recorded_at, metric_type,
                         value, unit, source, notes, created_at)
                    VALUES %s
                    """,
                    legacy_rows,
                )
            inserted_legacy = cur.rowcount

        if legacy_bp_rows:
            if has_bp_sync_dedupe_index(conn):
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_blood_pressure_readings
                        (tenant_id, id, user_id, measured_at,
                         systolic, diastolic, pulse, created_at)
                    VALUES %s
                    ON CONFLICT (tenant_id, user_id, measured_at, systolic, diastolic)
                    DO NOTHING
                    """,
                    legacy_bp_rows,
                )
            else:
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_blood_pressure_readings
                        (tenant_id, id, user_id, measured_at,
                         systolic, diastolic, pulse, created_at)
                    VALUES %s
                    """,
                    legacy_bp_rows,
                )
            inserted_legacy_bp = cur.rowcount

        conn.commit()
    except Exception:
        app.logger.exception("healthkit sync v2: failed legacy dual-write")
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Failed to store legacy health_metrics data'}), 500

    device_id = payload.get('device_id')
    anchors_map: dict[str, str] = {}
    if isinstance(device_id, str) and device_id:
        try:
            anchors_map = get_sync_anchors(cur, ctx, device_id)
        except Exception:
            app.logger.exception("healthkit sync v2: failed to read sync anchors")
            anchors_map = {}

    cur.close()
    conn.close()
    return jsonify({
        'payload_version': 2,
        'hkit': {
            'characteristics': counts.characteristics,
            'activity_summaries': counts.activity_summaries,
            'workouts': counts.workouts,
            'samples': counts.samples,
            'bp_correlations': counts.bp_correlations,
            'anchors': counts.anchors,
            'skipped': counts.skipped,
        },
        'legacy': {
            'inserted': inserted_legacy,
            'inserted_bp': inserted_legacy_bp,
        },
        'anchors': anchors_map,
        'received': len(payload.get('samples') or []),
    }), 200


@app.route('/api/v1/healthkit/sync', methods=['POST'])
@app.route('/api/v2/healthkit/sync', methods=['POST'])
@require_auth
def sync_healthkit():
    """Ingest HealthKit/health sync samples for the current user.

    Accepts two payload shapes:

    * ``payload_version`` absent or set to 1 — the legacy flat shape with
      a ``samples`` array of ``{type, value, unit, start_time, ...}`` dicts.
      Writes directly to ``health_metrics`` / ``health_blood_pressure_readings``.
      This path is unchanged from its historical behavior to avoid breaking
      existing mobile clients.

    * ``payload_version`` set to 2 — the canonical shape described in
      ``APIDocumentation/MobileAPI.md``. Writes to ``hkit_*``
      (via ``healthkit_writer``) AND also projects into the legacy
      ``health_metrics`` / ``health_blood_pressure_readings`` tables so
      the mobile UI keeps functioning during the transition. The legacy
      write under v2 is temporary; it will be replaced by a proper
      projector when Phase C of the HealthKit Consistency Plan lands.
    """
    payload = request.get_json(silent=True) or {}

    if payload.get('payload_version') == 2:
        return _sync_healthkit_v2(payload)

    samples = payload.get('samples') or []
    if not isinstance(samples, list):
        return jsonify({'error': 'samples must be an array'}), 400

    conn = get_user_db_connection()
    db_user_id = get_user_id()
    if not db_user_id:
        conn.close()
        return jsonify({'error': 'No valid user found for sync'}), 500

    tenant_raw = g.user.get('tenant_id', 1)
    try:
        tenant_id = int(tenant_raw) if tenant_raw not in (None, '') else 1
    except (TypeError, ValueError):
        tenant_id = 1

    cur = conn.cursor()
    rows = []
    bp_rows = []
    skipped = 0
    for sample in samples:
        if not isinstance(sample, dict):
            skipped += 1
            continue
        s_type = sample.get('type')
        if s_type == BLOOD_PRESSURE_SYNC_TYPE:
            start_time = sample.get('start_time')
            if not start_time:
                skipped += 1
                continue
            value = sample.get('value')
            systolic = None
            diastolic = None
            if isinstance(value, dict):
                systolic = value.get('systolic')
                diastolic = value.get('diastolic')
            if systolic is None and isinstance(value, (int, float, str)):
                systolic = value
            if systolic is None:
                systolic = sample.get('systolic')
            if diastolic is None:
                diastolic = sample.get('diastolic')
            _meta = sample.get('metadata')
            metadata = _meta if isinstance(_meta, dict) else {}
            if systolic is None:
                systolic = metadata.get('systolic')
            if diastolic is None:
                diastolic = metadata.get('diastolic')
            try:
                systolic = float(systolic)
                diastolic = float(diastolic)
            except (TypeError, ValueError):
                skipped += 1
                continue
            pulse = sample.get('heart_rate') or sample.get('heartRate') or metadata.get('heart_rate') or metadata.get('heartRate')
            try:
                pulse = float(pulse) if pulse is not None else None
            except (TypeError, ValueError):
                pulse = None
            try:
                measured_at = local_to_utc(start_time)
            except Exception:
                skipped += 1
                continue
            bp_rows.append((
                tenant_id,
                uuid.uuid4(),
                db_user_id,
                measured_at,
                systolic,
                diastolic,
                pulse,
                datetime.now(pytz.utc),
            ))
            continue

        if s_type not in HEALTH_SYNC_TYPES:
            skipped += 1
            continue
        start_time = sample.get('start_time')
        if not start_time:
            skipped += 1
            continue
        end_time = sample.get('end_time')
        try:
            recorded_at = local_to_utc(start_time)
        except Exception:
            skipped += 1
            continue

        value = sample.get('value')
        # Normalize numeric strings to floats; reject non-numeric types (dicts, lists)
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                value = None
        elif not isinstance(value, (int, float, type(None))):
            value = None
        unit = sample.get('unit')

        # Medication samples often come with a dosage string but no numeric value; store a default count
        # and preserve the dosage in raw metadata so inserts don't violate NOT NULL on value.
        if s_type == 'medication':
            if value is None:
                value = 1
            # Prefer explicit unit, otherwise fall back to dosage text or a generic label.
            unit = unit or sample.get('dosage') or 'dose'
            metadata = sample.get('metadata') or {}
            # Preserve medication identity details for display
            med_name = sample.get('medicationName') or sample.get('medication_name') or sample.get('name')
            if med_name and not metadata.get('medication_name'):
                metadata['medication_name'] = med_name
            if med_name and not metadata.get('medicationName'):
                metadata['medicationName'] = med_name
            if sample.get('status') and not metadata.get('status'):
                metadata['status'] = sample['status']
            if sample.get('dosage') and not metadata.get('dosage'):
                metadata['dosage'] = sample['dosage']
        else:
            metadata = sample.get('metadata')

        record = None
        if isinstance(metadata, dict) and isinstance(metadata.get('record'), dict):
            record = metadata.get('record')
        elif isinstance(sample.get('record'), dict):
            record = sample.get('record')

        if s_type in ('vital_sign_record', 'lab_result_record'):
            derived_readings = _extract_readings_from_record(record)
            for derived in derived_readings:
                derived_time = derived.get('timestamp') or start_time
                derived_meta = {'derived_from': s_type}
                if derived.get('display_name'):
                    derived_meta['record_display_name'] = derived.get('display_name')
                if derived.get('type') == 'blood_pressure':
                    try:
                        d_systolic = float(derived.get('systolic'))
                        d_diastolic = float(derived.get('diastolic'))
                    except (TypeError, ValueError):
                        continue
                    try:
                        measured_at = local_to_utc(derived_time)
                    except Exception:
                        continue
                    bp_rows.append((
                        tenant_id,
                        uuid.uuid4(),
                        db_user_id,
                        measured_at,
                        d_systolic,
                        d_diastolic,
                        None,
                        datetime.now(pytz.utc),
                    ))
                else:
                    derived_type = HEALTH_METRICS_TYPE_ALIASES.get(derived.get('type'), derived.get('type'))
                    if derived_type not in HEALTH_METRICS_ALLOWED_TYPES:
                        continue
                    try:
                        derived_recorded_at = local_to_utc(derived_time)
                    except Exception:
                        continue
                    derived_value = derived.get('value')
                    if isinstance(derived_value, str):
                        try:
                            derived_value = float(derived_value)
                        except ValueError:
                            continue
                    if not isinstance(derived_value, (int, float)):
                        continue
                    rows.append((
                        tenant_id,
                        uuid.uuid4(),
                        db_user_id,
                        derived_recorded_at,
                        derived_type,
                        derived_value,
                        # Normalize to the unit_conversion vocabulary so display
                        # and rollup convert it; unknown units stay raw (imports
                        # never drop data).
                        normalize_metric_unit(derived_type, derived.get('unit')) or derived.get('unit'),
                        sample.get('source'),
                        json.dumps({'metadata': derived_meta}),
                        datetime.now(pytz.utc),
                    ))

        if s_type in CLINICAL_SYNC_TYPES and value is None:
            value = 1
            unit = unit or 'record'

        # Skip samples that still don't have a usable value (keeps DB NOT NULL happy).
        if value is None:
            skipped += 1
            continue

        source = sample.get('source')
        raw_data = {'end_time': end_time, 'metadata': metadata} if end_time or metadata else None
        metric_type = HEALTH_METRICS_TYPE_ALIASES.get(s_type, s_type)
        if metric_type not in HEALTH_METRICS_ALLOWED_TYPES:
            skipped += 1
            continue
        rows.append((
            tenant_id,
            uuid.uuid4(),
            db_user_id,
            recorded_at,
            metric_type,
            value,
            # Normalize client unit spellings ('lb', 'degF') to the
            # unit_conversion vocabulary; unknown units stay raw.
            normalize_metric_unit(metric_type, unit) or unit,
            source,
            json.dumps(raw_data) if raw_data else None,
            datetime.now(pytz.utc),
        ))

    inserted = 0
    inserted_bp = 0
    if rows:
        try:
            if has_health_metrics_sync_dedupe_index(conn):
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_metrics (tenant_id, id, user_id, recorded_at, metric_type, value, unit, source, notes, created_at)
                    VALUES %s
                    ON CONFLICT (tenant_id, user_id, metric_type, recorded_at, value, unit, source) DO NOTHING
                    """,
                    rows,
                )
            else:
                # Fallback for environments where app-role cannot create the optional
                # dedupe index. Inserts remain functional without ON CONFLICT.
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_metrics (tenant_id, id, user_id, recorded_at, metric_type, value, unit, source, notes, created_at)
                    VALUES %s
                    """,
                    rows,
                )
            inserted = cur.rowcount
            conn.commit()
        except Exception:
            app.logger.exception("healthkit sync: failed to store health_metrics rows")
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'error': 'Failed to store health sync data'}), 500

    if bp_rows:
        try:
            if has_bp_sync_dedupe_index(conn):
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_blood_pressure_readings
                    (tenant_id, id, user_id, measured_at, systolic, diastolic, pulse, created_at)
                    VALUES %s
                    ON CONFLICT (tenant_id, user_id, measured_at, systolic, diastolic) DO NOTHING
                    """,
                    bp_rows,
                )
            else:
                db_driver.executemany_rows(
                    cur,
                    """
                    INSERT INTO health_blood_pressure_readings
                    (tenant_id, id, user_id, measured_at, systolic, diastolic, pulse, created_at)
                    VALUES %s
                    """,
                    bp_rows,
                )
            inserted_bp = cur.rowcount
            conn.commit()
        except Exception:
            app.logger.exception("healthkit sync: failed to store blood pressure rows")
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'error': 'Failed to store blood pressure sync data'}), 500

    cur.close()
    conn.close()
    return jsonify({
        'inserted': inserted,
        'inserted_bp': inserted_bp,
        'skipped': skipped,
        'received': len(samples),
    }), 200


# ==================== API ROUTES (BLUEPRINTS) ====================
# The following API routes have been moved to separate blueprint files in routes/:
#   - routes/food.py: /api/v1/food-items, /api/v1/meals
#   - routes/health_inputs.py: /api/v1/health-inputs, /api/v1/stacks, /api/v1/timeframes
#   - routes/logging_routes.py: /api/v1/log-*, /api/v1/all-logs, /api/v1/health-input-log, /api/v1/food-log
#   - routes/vitals.py: /api/v1/blood-pressure, /api/v1/temperature, /api/v1/weight, /api/v1/observations
#   - routes/analytics.py: /api/v1/your-week, /api/v1/sleep-heatmap, /api/v1/stress-heatmap,
#                          /api/v1/lab-results, /api/v1/diagnostics
#   - routes/integrations.py: /api/v1/healthkit/*, /api/v1/garmin/*
#   - routes/feedback.py: /api/v1/feedback
#
# Auth routes (/login, /logout, /signup, etc.) remain in this file.
# Blueprints are registered at the top of this file via routes/__init__.py


# ==================== CLI COMMANDS ====================
# Flask CLI extensions — run via: FLASK_APP=app flask <group> <command>

from api_docs import api_docs_cli
app.cli.add_command(api_docs_cli)


# ==================== MAIN ====================


if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=PORT, debug=debug)
