#!/usr/bin/env python3
"""
Minowa.ai Admin Tool - healthv10 version

Admin for the healthv10 home system (single household, single tenant).
No per-user databases or Postgres roles needed.

User creation is just:
  1. INSERT into users table
  2. INSERT default user_preferences
  Done!
"""

import sys
import os
import re
import uuid
from datetime import datetime

# db_driver shim lives in UserApp/webapp/ — put it on sys.path so this CLI
# uses the same psycopg3 driver wiring as the webapp.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp"))

try:
    import db_driver
    from argon2 import PasswordHasher
except ImportError as e:
    print(f"Error: Missing required module - {e.name}")
    print("")
    print("Install dependencies:")
    print("  pip install 'psycopg[binary,pool]' argon2-cffi python-dotenv")
    sys.exit(1)

# Load environment variables
from dotenv import load_dotenv
if os.path.exists('.env.local'):
    load_dotenv('.env.local')
else:
    load_dotenv()

# Configuration from .env
DB_NAME = os.getenv("DB_NAME", "healthv10")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "Password2026")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DEFAULT_TENANT_ID = int(os.getenv("DEFAULT_TENANT_ID", "1"))

# Password hasher using Argon2id
ph = PasswordHasher()


def get_connection():
    """Connect to healthv10 database via the db_driver shim.

    Row factory (dict-style rows) is set automatically by the shim.
    """
    return db_driver.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def validate_email(email):
    """Validate email format"""
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Invalid email format"
    if len(email) > 255:
        return False, "Email must be 255 characters or less"
    return True, None


def email_exists(email, tenant_id=None):
    """Check if email already exists in tenant"""
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE tenant_id = %s AND email = %s", (tenant_id, email.lower(),))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def get_user_by_email(email, tenant_id=None):
    """Look up user by email address within tenant"""
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tenant_id, id, email, display_name, is_active, created_at, last_login
        FROM users
        WHERE tenant_id = %s AND email = %s
    """, (tenant_id, email.lower().strip(),))
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else None


def provision_user(email, password, display_name):
    """
    Provision a new user in healthv10.

    Simple process:
      1. Validate inputs
      2. INSERT into users table
      3. INSERT default user_preferences
      Done! User data is scoped by user_id at the application layer.
    """
    # Validate email
    valid, error = validate_email(email)
    if not valid:
        print(f"Error: {error}")
        return False

    # Check if email exists
    if email_exists(email):
        print(f"Error: Email '{email}' is already registered")
        return False

    # Validate display name
    if not display_name or len(display_name.strip()) < 1:
        print("Error: Display name is required")
        return False
    display_name = display_name.strip()[:100]

    # Validate password
    if len(password) < 8:
        print("Error: Password must be at least 8 characters")
        return False

    print(f"\n{'='*60}")
    print(f"Provisioning user: {email}")
    print(f"Display name: {display_name}")
    print(f"{'='*60}\n")

    user_id = str(uuid.uuid4())
    password_hash = ph.hash(password)
    conn = None

    try:
        conn = get_connection()
        cur = conn.cursor()

        tenant_id = DEFAULT_TENANT_ID

        # 1. Create user record
        print("1. Creating user record...")
        cur.execute("""
            INSERT INTO users (tenant_id, id, email, display_name, password_hash, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
        """, (tenant_id, user_id, email.lower(), display_name, password_hash))
        print("   User record created")

        # 2. Create default user_preferences
        print("2. Creating default preferences...")
        cur.execute("""
            INSERT INTO user_preferences (tenant_id, user_id, created_at, updated_at)
            VALUES (%s, %s, NOW(), NOW())
        """, (tenant_id, user_id,))
        print("   Preferences created")

        # 3. Log audit entry
        print("3. Logging audit entry...")
        cur.execute("""
            INSERT INTO audit_log (tenant_id, user_id, action, details, created_at)
            VALUES (%s, %s, 'user_provisioned', %s, NOW())
        """, (tenant_id, user_id, f'{{"email": "{email}", "source": "admin_cli"}}'))
        print("   Audit logged")

        conn.commit()

        print(f"\n{'='*60}")
        print("User provisioned successfully!")
        print(f"{'='*60}\n")
        print("User Credentials:")
        print(f"  Email:        {email}")
        print(f"  Display Name: {display_name}")
        print(f"  Password:     {password}")
        print(f"  User ID:      {user_id}")
        print("\nDatabase: healthv10 (user data scoped by user_id at the application layer)")
        print()

        return True

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\nError during user provisioning: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def list_users():
    """List all users"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, display_name, created_at, last_login, is_active, is_developer
        FROM users
        ORDER BY created_at DESC
    """)
    users = cur.fetchall()
    conn.close()

    if not users:
        print("No users found")
        return

    print(f"\n{'='*108}")
    print(f"{'Email':<35} {'Display Name':<25} {'Created':<18} {'Active':<8} {'Dev':<5}")
    print(f"{'='*108}")
    for user in users:
        status = "Yes" if user['is_active'] else "No"
        dev = "Yes" if user.get('is_developer') else "No"
        created = user['created_at'].strftime("%Y-%m-%d %H:%M") if user['created_at'] else "N/A"
        email = user['email'] or "(none)"
        display_name = user['display_name'] or "(none)"
        print(f"{email:<35} {display_name:<25} {created:<18} {status:<8} {dev:<5}")
    print(f"{'='*100}\n")
    print(f"Total users: {len(users)}")
    print()


def disable_user(email):
    """Disable a user account"""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = false WHERE tenant_id = %s AND id = %s", (tenant_id, user['id'],))
    cur.execute("DELETE FROM sessions WHERE tenant_id = %s AND user_id = %s", (tenant_id, user['id'],))
    conn.commit()
    conn.close()

    print(f"User '{email}' disabled and all sessions terminated")
    return True


def enable_user(email):
    """Enable a user account"""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = true WHERE tenant_id = %s AND id = %s", (tenant_id, user['id'],))
    conn.commit()
    conn.close()

    print(f"User '{email}' enabled")
    return True


def set_developer(email):
    """Grant developer flag to a user account"""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_developer = true WHERE tenant_id = %s AND id = %s", (tenant_id, user['id'],))
    conn.commit()
    conn.close()

    print(f"User '{email}' granted developer access")
    return True


def unset_developer(email):
    """Revoke developer flag from a user account"""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_developer = false WHERE tenant_id = %s AND id = %s", (tenant_id, user['id'],))
    conn.commit()
    conn.close()

    print(f"User '{email}' developer access revoked")
    return True


def reset_password(email, new_password):
    """Reset user password. Re-enables disabled accounts and terminates existing sessions."""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    was_disabled = not user.get('is_active', True)
    password_hash = ph.hash(new_password)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET password_hash = %s, is_active = true, updated_at = NOW()
        WHERE tenant_id = %s AND id = %s
    """, (password_hash, tenant_id, user['id']))
    cur.execute("DELETE FROM sessions WHERE tenant_id = %s AND user_id = %s", (tenant_id, user['id']))
    conn.commit()
    conn.close()

    if was_disabled:
        print(f"Password reset for user '{email}' (account was disabled — now re-enabled)")
    else:
        print(f"Password reset for user '{email}'")
    print(f"  New password: {new_password}")
    print("  All existing sessions terminated")
    return True


def delete_user(email, force=False):
    """Delete a user (cascades via FK constraints)"""
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    if not force:
        response = input(f"Delete user '{email}' and ALL their data? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted")
            return False

    tenant_id = user.get('tenant_id', DEFAULT_TENANT_ID)
    print(f"\n{'='*60}")
    print(f"Deleting user: {email}")
    print(f"{'='*60}\n")

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Detach audit_log entries — preserve the record but remove the FK
        # (audit_log FK is ON DELETE SET NULL, but tenant_id is NOT NULL, so
        # we must handle it explicitly before deleting the user)
        print("1. Detaching audit log entries...")
        cur.execute("""
            DELETE FROM audit_log
            WHERE tenant_id = %s AND user_id = %s
        """, (tenant_id, user['id']))
        print("   Audit log entries removed")

        # Delete user (FK CASCADE handles all other related data)
        print("2. Deleting user record (cascades to all user data)...")
        cur.execute("DELETE FROM users WHERE tenant_id = %s AND id = %s", (tenant_id, user['id'],))
        conn.commit()
        print("   User deleted")

        conn.close()

        print(f"\n{'='*60}")
        print(f"User '{email}' deleted successfully!")
        print(f"{'='*60}\n")
        return True

    except Exception as e:
        print(f"\nError during deletion: {e}")
        import traceback
        traceback.print_exc()
        return False


def count_user_records(email, tenant_id=None):
    """Report row counts for every table that has rows owned by this user.

    Discovers user-data tables dynamically (any public table with both a
    tenant_id and a user_id column), then counts rows per table where
    (tenant_id, user_id) match the resolved user. Connects as superuser
    (POSTGRES_USER) — appropriate for admin diagnostics.

    Output is sorted by count descending; zero-count tables print at the end.
    """
    from psycopg import sql

    user = get_user_by_email(email, tenant_id=tenant_id)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    resolved_tenant = user['tenant_id']
    user_id = user['id']

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT c1.table_name
        FROM information_schema.columns c1
        JOIN information_schema.columns c2
          ON c1.table_schema = c2.table_schema
         AND c1.table_name = c2.table_name
        WHERE c1.table_schema = 'public'
          AND c1.column_name = 'tenant_id'
          AND c2.column_name = 'user_id'
        ORDER BY c1.table_name
    """)
    tables = [row['table_name'] for row in cur.fetchall()]

    counts = []
    for table in tables:
        query = sql.SQL(
            "SELECT count(*) AS n FROM {} WHERE tenant_id = %s AND user_id = %s"
        ).format(sql.Identifier(table))
        cur.execute(query, (resolved_tenant, user_id))
        counts.append((table, cur.fetchone()['n']))

    conn.close()

    counts.sort(key=lambda r: (-r[1], r[0]))
    total = sum(n for _, n in counts)
    nonempty = sum(1 for _, n in counts if n > 0)

    display_name = user.get('display_name') or '(no display name)'
    print(f"\n{'='*60}")
    print(f"User:        {email}")
    print(f"Display:     {display_name}")
    print(f"Tenant / ID: {resolved_tenant} / {user_id}")
    print(f"{'='*60}")
    print(f"{'Table':<40} {'Rows':>15}")
    print(f"{'-'*60}")
    for table, n in counts:
        print(f"{table:<40} {n:>15,}")
    print(f"{'-'*60}")
    print(f"{'TOTAL':<40} {total:>15,}")
    print(f"\nScanned {len(tables)} tables, {nonempty} contained rows for this user.\n")
    return True


def issue_api_key(email, label):
    """Issue a long-lived API key for a user. Token is shown once."""
    import auth as _auth
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    key_id, result = _auth.create_api_key(
        user_id=str(user['id']),
        tenant_id=user.get('tenant_id', DEFAULT_TENANT_ID),
        label=label,
    )
    if key_id is None:
        print(f"Error issuing API key: {result}")
        return False

    print(f"\n{'='*60}")
    print(f"API key issued for {email}")
    print(f"{'='*60}\n")
    print(f"  Label:   {label}")
    print(f"  Key ID:  {key_id}")
    print(f"  Token:   {result}")
    print("\nStore this token now — it will not be shown again.\n")
    return True


def list_api_keys_cmd(email):
    """List active API keys for a user (metadata only)."""
    import auth as _auth
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    keys = _auth.list_api_keys(
        user_id=str(user['id']),
        tenant_id=user.get('tenant_id', DEFAULT_TENANT_ID),
    )
    if not keys:
        print(f"No active API keys for {email}")
        return True

    print(f"\n{'='*78}")
    print(f"{'Key ID':<38} {'Prefix':<14} {'Label':<14} {'Created':<10}")
    print(f"{'='*78}")
    for k in keys:
        created = k['created_at'].strftime("%Y-%m-%d") if k.get('created_at') else "N/A"
        prefix = k.get('key_prefix') or "(none)"
        label = (k.get('device_name') or "(none)")[:14]
        print(f"{str(k['id']):<38} {prefix:<14} {label:<14} {created:<10}")
    print(f"{'='*78}\n")
    return True


def revoke_api_key_cmd(email, key_id):
    """Revoke an API key by its UUID."""
    import auth as _auth
    user = get_user_by_email(email)
    if not user:
        print(f"Error: User '{email}' not found")
        return False

    success, err = _auth.revoke_api_key(
        key_id=key_id,
        user_id=str(user['id']),
        tenant_id=user.get('tenant_id', DEFAULT_TENANT_ID),
    )
    if not success:
        print(f"Error revoking key: {err}")
        return False
    print(f"API key {key_id} revoked for {email}")
    return True


def show_help():
    """Show help message"""
    print("""
Minowa.ai Admin Tool (healthv10)

Usage:
  ./admin.py provision-user <email> <password> <display_name>  Create a new user
  ./admin.py list-users                                        List all users
  ./admin.py count-records <email>                             Show row counts per table for a user
  ./admin.py delete-user <email>                               Delete a user and all data
  ./admin.py disable-user <email>                              Disable a user
  ./admin.py enable-user <email>                               Enable a user
  ./admin.py set-developer <email>                             Grant developer access
  ./admin.py unset-developer <email>                           Revoke developer access
  ./admin.py reset-password <email> <password>                 Reset user password
  ./admin.py issue-api-key <email> <label>                     Issue a long-lived API key (shown once)
  ./admin.py list-api-keys <email>                             List active API keys for a user
  ./admin.py revoke-api-key <email> <key_id>                   Revoke an API key by ID
  ./admin.py help                                              Show this help

Examples:
  ./admin.py provision-user alice@example.com MySecurePass123 "Alice Smith"
  ./admin.py list-users
  ./admin.py count-records alice@example.com
  ./admin.py disable-user alice@example.com
  ./admin.py reset-password alice@example.com NewPassword456
  ./admin.py delete-user alice@example.com

Notes:
  - Users are identified by email address
  - All users share the healthv10 database (isolation via app-level user_id scoping)
  - No per-user databases or Postgres roles needed
""")


def main():
    if len(sys.argv) < 2:
        show_help()
        sys.exit(1)

    command = sys.argv[1]

    if command == "provision-user":
        if len(sys.argv) != 5:
            print("Usage: ./admin.py provision-user <email> <password> <display_name>")
            print("Example: ./admin.py provision-user alice@example.com MyPass123 \"Alice Smith\"")
            sys.exit(1)
        if not provision_user(sys.argv[2], sys.argv[3], sys.argv[4]):
            sys.exit(1)

    elif command == "list-users":
        list_users()

    elif command == "count-records":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py count-records <email>")
            sys.exit(1)
        if not count_user_records(sys.argv[2]):
            sys.exit(1)

    elif command == "disable-user":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py disable-user <email>")
            sys.exit(1)
        if not disable_user(sys.argv[2]):
            sys.exit(1)

    elif command == "enable-user":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py enable-user <email>")
            sys.exit(1)
        if not enable_user(sys.argv[2]):
            sys.exit(1)

    elif command == "set-developer":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py set-developer <email>")
            sys.exit(1)
        if not set_developer(sys.argv[2]):
            sys.exit(1)

    elif command == "unset-developer":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py unset-developer <email>")
            sys.exit(1)
        if not unset_developer(sys.argv[2]):
            sys.exit(1)

    elif command == "reset-password":
        if len(sys.argv) != 4:
            print("Usage: ./admin.py reset-password <email> <password>")
            sys.exit(1)
        if not reset_password(sys.argv[2], sys.argv[3]):
            sys.exit(1)

    elif command == "delete-user":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py delete-user <email>")
            sys.exit(1)
        if not delete_user(sys.argv[2]):
            sys.exit(1)

    elif command == "issue-api-key":
        if len(sys.argv) != 4:
            print("Usage: ./admin.py issue-api-key <email> <label>")
            sys.exit(1)
        if not issue_api_key(sys.argv[2], sys.argv[3]):
            sys.exit(1)

    elif command == "list-api-keys":
        if len(sys.argv) != 3:
            print("Usage: ./admin.py list-api-keys <email>")
            sys.exit(1)
        if not list_api_keys_cmd(sys.argv[2]):
            sys.exit(1)

    elif command == "revoke-api-key":
        if len(sys.argv) != 4:
            print("Usage: ./admin.py revoke-api-key <email> <key_id>")
            sys.exit(1)
        if not revoke_api_key_cmd(sys.argv[2], sys.argv[3]):
            sys.exit(1)

    elif command == "help":
        show_help()

    else:
        print(f"Unknown command: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
