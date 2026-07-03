"""Synthetic-fixture tests for Track 3 (Flask-route AST rules).

These tests exercise the rules added to ``code_query_audit.py`` by Track 3
of [SecurityHardening.md](../SecurityHardening.md):

    Rule 1 — CSRF missing on a session-auth mutating route → warning
    Rule 2 — ``send_from_directory('.', ...)`` → error
    Rule 4 — SQL write to a sensitive-named column from a function not in
             ``Compliance/sensitive-write-sites.md`` → error

Each test writes a small Python source file to a tmp directory, runs the
relevant pieces of ``code_query_audit`` against it, and asserts the
expected ``Finding`` fires (or does not). No database, no network — pure
AST + parsing.

Run:
    .venv/bin/python -m pytest DataModel3/test_route_audit.py -v
"""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from code_query_audit import (  # noqa: E402
    RECOGNISED_ALGOS,
    SCHEMA,
    AllowlistEntry,
    SqlSite,
    extract_crypto_contract_findings,
    extract_permissive_default_findings,
    extract_route_findings,
    extract_sensitive_write_findings,
    extract_sites,
    parse_algo_from_comment,
    parse_column_comments,
    parse_route_audit_allowlist,
    parse_schema,
    parse_sensitive_writes_inventory,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


# ---------------------------------------------------------------------------
# Rule 2 — send_from_directory('.', ...)
# ---------------------------------------------------------------------------

class TestRule2SendFromDirRoot:
    def test_fires_on_dot_base(self, tmp_path: Path):
        f = _write(tmp_path, "rule2_violator.py", """
            from flask import send_from_directory

            def serve():
                return send_from_directory('.', 'index.html')
        """)
        findings = extract_route_findings(f, allowlist={})
        cats = [x.category for x in findings]
        assert "send_from_dir_root" in cats

    def test_silent_on_real_static_dir(self, tmp_path: Path):
        f = _write(tmp_path, "rule2_clean.py", """
            from flask import send_from_directory

            def serve():
                return send_from_directory('static', 'index.html')
        """)
        findings = extract_route_findings(f, allowlist={})
        cats = [x.category for x in findings]
        assert "send_from_dir_root" not in cats

    def test_allowlist_silences_dot_base(self, tmp_path: Path):
        f = _write(tmp_path, "rule2_allowlisted.py", """
            from flask import send_from_directory

            def index():
                return send_from_directory('.', 'index.html')
        """)
        rel = str(f.name)  # extract_route_findings normalises against REPO;
                            # for tmp paths outside repo it falls back to str(file)
        # Build an allowlist that matches by enclosing function name. The scope
        # form is `func:<rel_path>:<func>` — for tmp_path the relative path
        # comparison falls back to the raw str(file), so we craft accordingly.
        allow = {
            "send_from_dir_root": [AllowlistEntry(
                rule="send_from_dir_root",
                scope=f"func:{f}:index",
                reason="test",
                tracked_in="test",
            )],
        }
        # The auditor's matcher uses rel_file == scope's file portion. Inside
        # this test we drive the matcher directly via .matches().
        entry = allow["send_from_dir_root"][0]
        assert entry.matches(str(f), "index")
        # Also check that a non-matching function still fires:
        assert not entry.matches(str(f), "other_function")


# ---------------------------------------------------------------------------
# Rule 1 — CSRF missing on a session-auth mutating route (warning)
# ---------------------------------------------------------------------------

class TestRule1CsrfMissing:
    def test_fires_on_session_auth_post(self, tmp_path: Path):
        f = _write(tmp_path, "rule1_violator.py", """
            from flask import Blueprint
            bp = Blueprint('x', __name__)

            def require_auth(fn):
                return fn

            @bp.route('/api/v1/synthetic', methods=['POST'])
            @require_auth
            def synthetic_session_auth():
                return {}, 200
        """)
        findings = extract_route_findings(f, allowlist={})
        rule1 = [x for x in findings if x.category == "csrf_missing"]
        assert rule1, "expected a csrf_missing warning"
        assert rule1[0].severity == "warning", "Rule 1 stays at warning until CSRFProtect rolls out"

    def test_silent_for_bearer_auth_route(self, tmp_path: Path):
        f = _write(tmp_path, "rule1_bearer.py", """
            from flask import Blueprint
            bp = Blueprint('x', __name__)

            def require_bearer_token(fn):
                return fn

            @bp.route('/api/v1/synthetic', methods=['POST'])
            @require_bearer_token
            def synthetic_bearer():
                return {}, 200
        """)
        findings = extract_route_findings(f, allowlist={})
        cats = [x.category for x in findings]
        assert "csrf_missing" not in cats

    def test_silent_with_csrf_decorator(self, tmp_path: Path):
        f = _write(tmp_path, "rule1_csrf.py", """
            from flask import Blueprint
            bp = Blueprint('x', __name__)

            class _Csrf:
                def protect(self, fn):
                    return fn
            csrf = _Csrf()

            @bp.route('/api/v1/synthetic', methods=['POST'])
            @csrf.protect
            def synthetic_csrf():
                return {}, 200
        """)
        findings = extract_route_findings(f, allowlist={})
        cats = [x.category for x in findings]
        assert "csrf_missing" not in cats


# ---------------------------------------------------------------------------
# Rule 4 — sensitive-column write from an unlisted function
# ---------------------------------------------------------------------------

class TestRule4SensitiveWrite:
    def test_fires_on_unlisted_password_hash_insert(self, tmp_path: Path):
        f = _write(tmp_path, "rule4_violator.py", """
            def synthetic_unlisted_writer(cur, uid, h):
                cur.execute('''
                    INSERT INTO users (id, password_hash) VALUES (%s, %s)
                ''', (uid, h))
        """)
        sites = extract_sites(f)
        assert sites, "expected SQL site to be extracted"
        trees = {str(f): ast.parse(f.read_text(), filename=str(f))}
        findings = []
        for s in sites:
            findings.extend(extract_sensitive_write_findings(s, trees, approved_sites=set()))
        cats = [x.category for x in findings]
        assert "sensitive_write" in cats

    def test_silent_when_function_in_inventory(self, tmp_path: Path):
        f = _write(tmp_path, "rule4_listed.py", """
            def listed_writer(cur, uid, h):
                cur.execute('''
                    INSERT INTO users (id, password_hash) VALUES (%s, %s)
                ''', (uid, h))
        """)
        sites = extract_sites(f)
        trees = {str(f): ast.parse(f.read_text(), filename=str(f))}
        # Mark the function as "approved" by adding it to the set — Rule 4
        # uses (func_name, rel_file) tuples; rel_file is the path relative
        # to REPO, but for files outside REPO the auditor falls back to the
        # absolute string path, which is what we feed in here.
        approved = {("listed_writer", str(f))}
        findings = []
        for s in sites:
            findings.extend(extract_sensitive_write_findings(s, trees, approved))
        cats = [x.category for x in findings]
        assert "sensitive_write" not in cats

    def test_silent_for_non_sensitive_column(self, tmp_path: Path):
        f = _write(tmp_path, "rule4_innocent.py", """
            def innocent(cur, uid, name):
                cur.execute('''
                    INSERT INTO users (id, display_name) VALUES (%s, %s)
                ''', (uid, name))
        """)
        sites = extract_sites(f)
        trees = {str(f): ast.parse(f.read_text(), filename=str(f))}
        findings = []
        for s in sites:
            findings.extend(extract_sensitive_write_findings(s, trees, approved_sites=set()))
        cats = [x.category for x in findings]
        assert "sensitive_write" not in cats

    def test_fires_on_update_to_token_column(self, tmp_path: Path):
        f = _write(tmp_path, "rule4_update.py", """
            def update_token(cur, uid, t):
                cur.execute('''
                    UPDATE api_tokens SET token_hash = %s WHERE user_id = %s
                ''', (t, uid))
        """)
        sites = extract_sites(f)
        trees = {str(f): ast.parse(f.read_text(), filename=str(f))}
        findings = []
        for s in sites:
            findings.extend(extract_sensitive_write_findings(s, trees, approved_sites=set()))
        cats = [x.category for x in findings]
        assert "sensitive_write" in cats


# ---------------------------------------------------------------------------
# Track 2 — permissive-default heuristic warnings
# ---------------------------------------------------------------------------

def _ast_and_sites(file: Path) -> tuple[ast.Module, list[SqlSite]]:
    return ast.parse(file.read_text(), filename=str(file)), extract_sites(file)


class TestTrack2Pattern1FunctionNames:
    @pytest.mark.parametrize("fname", [
        "get_first_user_record",
        "get_default_user",
        "fallback_user",
        "_default_user_lookup",
        "find_user_or_anon",
        "lookup_or_admin",
    ])
    def test_fires_on_suspicious_name(self, tmp_path: Path, fname: str):
        f = _write(tmp_path, "perm1_violator.py", f"""
            def {fname}():
                return None
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats_with_p1 = [
            x for x in findings
            if x.category == "permissive_default" and "name pattern" in x.message
        ]
        assert cats_with_p1, f"expected Pattern 1 hit on {fname}, got {findings}"

    def test_silent_on_normal_name(self, tmp_path: Path):
        f = _write(tmp_path, "perm1_clean.py", """
            def get_user_by_email(email):
                return None
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        assert not findings


class TestTrack2Pattern2OrderByLimit1:
    def test_fires_in_suspicious_function(self, tmp_path: Path):
        f = _write(tmp_path, "perm2_violator.py", """
            def get_first_user_record(cur):
                cur.execute('SELECT id FROM users ORDER BY id LIMIT 1')
                return cur.fetchone()
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if x.category == "permissive_default" and "ORDER BY" in x.message
        ]
        assert cats, f"expected Pattern 2 hit, got {findings}"

    def test_fires_in_require_auth_route(self, tmp_path: Path):
        f = _write(tmp_path, "perm2_route.py", """
            def require_auth(fn):
                return fn

            @require_auth
            def some_route(cur, user_id):
                cur.execute('SELECT * FROM items WHERE user_id=%s ORDER BY ts LIMIT 1')
                return cur.fetchone()
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if x.category == "permissive_default" and "ORDER BY" in x.message
        ]
        assert cats, "expected Pattern 2 hit on @require_auth route"

    def test_silent_outside_auth_context(self, tmp_path: Path):
        # Helper function with no decorator and no auth signals — purely
        # "fetch the latest row". Pattern 2 should NOT fire.
        f = _write(tmp_path, "perm2_innocent.py", """
            def latest_inventory(cur):
                cur.execute('SELECT id FROM items ORDER BY received_at DESC LIMIT 1')
                return cur.fetchone()
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        # Pattern 2 should not fire; Pattern 1 should not fire either.
        assert not findings


class TestTrack2Pattern3BareExceptReturn:
    def test_fires_on_bare_except_returning_value(self, tmp_path: Path):
        f = _write(tmp_path, "perm3_violator.py", """
            def lookup(cur, email):
                try:
                    cur.execute('SELECT id FROM users WHERE email=%s', (email,))
                    return cur.fetchone()
                except:
                    return {'id': 'fallback'}
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if x.category == "permissive_default" and "Bare `except:`" in x.message
        ]
        assert cats, f"expected Pattern 3 hit, got {findings}"

    def test_silent_on_typed_except(self, tmp_path: Path):
        f = _write(tmp_path, "perm3_typed.py", """
            def lookup(cur, email):
                try:
                    cur.execute('SELECT id FROM users WHERE email=%s', (email,))
                    return cur.fetchone()
                except KeyError:
                    return {'id': 'fallback'}
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        # Typed except is not flagged — only bare `except:`.
        cats = [
            x for x in findings
            if "Bare `except:`" in x.message
        ]
        assert not cats

    def test_silent_on_bare_except_with_raise(self, tmp_path: Path):
        f = _write(tmp_path, "perm3_reraise.py", """
            def lookup(cur, email):
                try:
                    cur.execute('SELECT id FROM users WHERE email=%s', (email,))
                    return cur.fetchone()
                except:
                    raise
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if "Bare `except:`" in x.message
        ]
        assert not cats

    def test_silent_on_bare_except_returning_error_tuple(self, tmp_path: Path):
        f = _write(tmp_path, "perm3_error_return.py", """
            def lookup(cur, email):
                try:
                    cur.execute('SELECT id FROM users WHERE email=%s', (email,))
                    return cur.fetchone()
                except:
                    return False, "database error"
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [x for x in findings if "Bare `except:`" in x.message]
        assert not cats, "(False, 'msg') is recognised as an error return"


class TestTrack2Pattern4IfNotAuth:
    def test_fires_on_g_user_with_permissive_return(self, tmp_path: Path):
        f = _write(tmp_path, "perm4_violator.py", """
            from flask import g

            def synthetic():
                if not g.user:
                    return {'id': 'anonymous-fallback'}
                return g.user
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if "if not g.user" in x.message
        ]
        assert cats, f"expected Pattern 4 hit, got {findings}"

    def test_silent_on_g_user_with_error_return(self, tmp_path: Path):
        f = _write(tmp_path, "perm4_clean.py", """
            from flask import g, jsonify

            def synthetic():
                if not g.user:
                    return jsonify({'error': 'unauthorized'}), 401
                return g.user
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        cats = [
            x for x in findings
            if "if not g.user" in x.message
        ]
        assert not cats

    def test_silent_on_bare_user_var(self, tmp_path: Path):
        # Bare `user` is too generic — auth.py and CLI tools have lots of
        # `if not user: return False, "Not found"`. Pattern 4 only fires
        # on qualified attribute accesses (g.user, current_user, etc.).
        f = _write(tmp_path, "perm4_bare_user.py", """
            def lookup(cur, email):
                cur.execute('SELECT id FROM users WHERE email=%s', (email,))
                user = cur.fetchone()
                if not user:
                    return False, "User not found"
                return True, user
        """)
        tree, sites = _ast_and_sites(f)
        findings = extract_permissive_default_findings(f, tree, sites)
        # Should be silent — `user` is too generic and the return is an
        # error tuple anyway.
        cats = [x for x in findings if "if not" in x.message]
        assert not cats


class TestTrack2RealRepoCoverage:
    """The real-repo coverage test — Track 2 must catch the F3 source."""

    def test_get_first_user_record_is_flagged(self):
        repo = Path(__file__).resolve().parent.parent
        utils_path = repo / "UserApp/webapp/utils.py"
        if not utils_path.exists():
            pytest.skip("UserApp/webapp/utils.py not present")
        tree, sites = _ast_and_sites(utils_path)
        findings = extract_permissive_default_findings(utils_path, tree, sites)
        # We expect both Pattern 1 (name) and Pattern 2 (SQL) to fire on
        # the F3 source — the whole reason Track 2 exists.
        names = [x for x in findings if "name pattern" in x.message]
        sql_hits = [x for x in findings if "ORDER BY" in x.message]
        assert any("get_first_user_record" in n.message for n in names), (
            "Pattern 1 must flag get_first_user_record"
        )
        assert any("get_first_user_record" in s.message for s in sql_hits), (
            "Pattern 2 must flag the SELECT in get_first_user_record"
        )


# ---------------------------------------------------------------------------
# Track 7 — schema column-comment crypto contract
# ---------------------------------------------------------------------------

class TestTrack7AlgoParser:
    @pytest.mark.parametrize("comment,expected", [
        ("algo: argon2id — Argon2id PHC string", "argon2id"),
        ("algo:bcrypt", "bcrypt"),
        ("algo:   sha256   ", "sha256"),
        ("ALGO: fernet — case-insensitive", "fernet"),
        ("Some preamble. algo: aes-gcm — explanation.", "aes-gcm"),
        ("Just a regular comment.", None),
        ("", None),
    ])
    def test_extracts_algo(self, comment, expected):
        assert parse_algo_from_comment(comment) == expected


class TestTrack7CryptoContract:
    def test_no_findings_when_all_columns_annotated(self):
        # Synthesise a tiny "schema" snapshot with full coverage.
        tables = {"users": {"id", "password_hash"}}
        comments = {
            ("users", "password_hash"): "algo: argon2id — written by hash_password()",
        }
        findings = extract_crypto_contract_findings(tables, comments)
        assert findings == []

    def test_missing_comment_fires(self):
        tables = {"users": {"id", "password_hash"}}
        comments: dict[tuple[str, str], str] = {}
        findings = extract_crypto_contract_findings(tables, comments)
        cats = [f.category for f in findings]
        assert "crypto_contract_missing" in cats
        assert any("password_hash" in f.message for f in findings)

    def test_comment_without_algo_directive_fires(self):
        tables = {"users": {"password_hash"}}
        comments = {
            ("users", "password_hash"): "Hashed password column. (no algo directive)",
        }
        findings = extract_crypto_contract_findings(tables, comments)
        assert any(
            f.category == "crypto_contract_missing" for f in findings
        )

    def test_unknown_algo_fires(self):
        tables = {"users": {"password_hash"}}
        comments = {
            ("users", "password_hash"): "algo: rot13 — bring on the funk",
        }
        findings = extract_crypto_contract_findings(tables, comments)
        cats = [f.category for f in findings]
        assert "crypto_contract_unknown" in cats

    def test_non_sensitive_columns_skipped(self):
        # `email` is not a sensitive-pattern column — no annotation needed.
        tables = {"users": {"id", "email"}}
        comments: dict[tuple[str, str], str] = {}
        findings = extract_crypto_contract_findings(tables, comments)
        assert findings == []

    def test_recognised_algos_include_required_set(self):
        # Sanity check on the vocabulary — these are referenced in the
        # schema annotations and the failure of any of them to be in the
        # set would cause crypto_contract_unknown errors at boot.
        for algo in ("argon2id", "bcrypt", "sha256", "fernet", "aes-gcm",
                     "plaintext", "tbd", "not-a-credential"):
            assert algo in RECOGNISED_ALGOS


class TestTrack7RealSchema:
    """Real-schema coverage — every sensitive column in the live schema
    must have a recognised algo annotation. This is the test that
    actually gates the Track 7 contract: if anyone adds a new
    sensitive-pattern column without a COMMENT ON COLUMN, this test
    fails."""

    def test_live_schema_has_algo_for_every_sensitive_column(self):
        if not SCHEMA.exists():
            pytest.skip("schema source-of-truth file not present")
        tables, _ = parse_schema(SCHEMA)
        comments = parse_column_comments(SCHEMA)
        findings = extract_crypto_contract_findings(tables, comments)
        assert findings == [], (
            "Track 7 contract violated — every sensitive-pattern column "
            "must have a COMMENT ON COLUMN with `algo: <name>`. Failures:\n"
            + "\n".join(f"  {f.category}: {f.message}" for f in findings)
        )

    def test_known_columns_have_expected_algos(self):
        if not SCHEMA.exists():
            pytest.skip("schema source-of-truth file not present")
        comments = parse_column_comments(SCHEMA)
        # Spot-check a few columns whose algo is locked in by their
        # writer's helper. Failure here means the schema annotation drifted
        # away from what the code actually does.
        # Spot-check only columns that exist in the home schema.
        cases = [
            (("users", "password_hash"), "argon2id"),
            (("users", "totp_secret"), "plaintext"),
            (("api_tokens", "token_hash"), "sha256"),
            (("garmin_credentials", "encrypted_password"), "tbd"),
        ]
        for (tbl, col), expected in cases:
            comment = comments.get((tbl, col))
            assert comment is not None, f"missing comment for {tbl}.{col}"
            algo = parse_algo_from_comment(comment)
            assert algo == expected, (
                f"{tbl}.{col}: expected algo={expected!r}, got {algo!r} "
                f"in comment {comment!r}"
            )


# ---------------------------------------------------------------------------
# Allowlist + inventory parsers — the markdown-table parsing layer.
# ---------------------------------------------------------------------------

class TestAllowlistParser:
    def test_parses_real_allowlist_file(self):
        """The committed allowlist should round-trip without errors."""
        repo = Path(__file__).resolve().parent.parent
        allowlist_path = repo / "Compliance/route-audit-allowlist.md"
        if not allowlist_path.exists():
            pytest.skip("route-audit-allowlist.md not present")
        out = parse_route_audit_allowlist(allowlist_path)
        # Every rule id from the auditor's vocabulary is represented as a key.
        for rule_id in ("csrf_missing", "send_from_dir_root",
                        "sensitive_write"):
            assert rule_id in out, f"missing parsed section for {rule_id}"
        # The Rule 2 section is non-empty in the committed file.
        assert out["send_from_dir_root"], "expected at least one Rule 2 entry"

    def test_parses_real_inventory_file(self):
        repo = Path(__file__).resolve().parent.parent
        inv_path = repo / "Compliance/sensitive-write-sites.md"
        if not inv_path.exists():
            pytest.skip("sensitive-write-sites.md not present")
        approved = parse_sensitive_writes_inventory(inv_path)
        # The Garmin write site (F2) must be in the inventory — that's the
        # whole reason the inventory exists.
        assert ("garmin_connect", "UserApp/webapp/routes/integrations.py") in approved

    def test_dir_scope_matches_subpath(self):
        e = AllowlistEntry(
            rule="csrf_missing",
            scope="dir:UserApp/",
            reason="test", tracked_in="test",
        )
        assert e.matches("UserApp/webapp/routes/x.py", "any_func")
        assert not e.matches("OtherApp/app.py", "any_func")

    def test_file_scope_matches_exact(self):
        e = AllowlistEntry(
            rule="csrf_missing",
            scope="file:UserApp/webapp/app.py",
            reason="test", tracked_in="test",
        )
        assert e.matches("UserApp/webapp/app.py", "any_func")
        assert not e.matches("UserApp/webapp/auth.py", "any_func")

    def test_func_scope_requires_exact_func_name(self):
        e = AllowlistEntry(
            rule="csrf_missing",
            scope="func:UserApp/webapp/app.py:api_login",
            reason="test", tracked_in="test",
        )
        assert e.matches("UserApp/webapp/app.py", "api_login")
        assert not e.matches("UserApp/webapp/app.py", "api_logout")
        assert not e.matches("UserApp/webapp/app.py", None)
