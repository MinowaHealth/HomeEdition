"""
Migration verification tests.

Run these after v8→v10 migration to verify data integrity.
These tests connect directly to PostgreSQL to check row counts
and FK relationships.

Usage:
    TEST_DB_HOST=localhost TEST_DB_PASSWORD=Password2026 pytest tests/test_migration.py -v

These tests would have caught the stack_inputs column mapping bug.
"""
import os
import pytest
import psycopg

# Skip entire module if DB not configured for integration testing.
if os.getenv("RUN_INTEGRATION_TESTS", "0").strip().lower() not in ("1", "true", "yes"):
    pytest.skip(
        "Migration integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def db_conn():
    """Direct database connection for migration verification."""
    conn = psycopg.connect(
        host=os.getenv("TEST_DB_HOST", "localhost"),
        port=int(os.getenv("TEST_DB_PORT", "5432")),
        dbname=os.getenv("TEST_DB_NAME", "healthv10"),
        user=os.getenv("TEST_DB_USER", "postgres"),
        password=os.getenv("TEST_DB_PASSWORD", "Password2026"),
    )
    yield conn
    conn.close()


class TestMigrationRowCounts:
    """Verify expected tables have data after migration."""

    # Tables that MUST have data after migration
    REQUIRED_TABLES = [
        ("users", 1),           # At least 1 user
        ("health_inputs", 10),  # Expect reasonable data
        ("stacks", 1),          # At least 1 stack
        ("stack_inputs", 1),    # THIS IS THE ONE THAT FAILED
        ("timeframes", 1),
    ]

    # Tables that may be empty but should exist
    OPTIONAL_TABLES = [
        "health_input_log",
        "health_observations",
        "health_food_logv2",
        "garm_stress",
        "garm_hr",
    ]

    @pytest.mark.parametrize("table,min_rows", REQUIRED_TABLES)
    def test_required_table_has_data(self, db_conn, table, min_rows):
        """Verify required tables have minimum expected rows."""
        cur = db_conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        cur.close()

        assert count >= min_rows, (
            f"Table '{table}' has {count} rows, expected at least {min_rows}. "
            f"Migration may have failed for this table."
        )

    @pytest.mark.parametrize("table", OPTIONAL_TABLES)
    def test_optional_table_exists(self, db_conn, table):
        """Verify optional tables exist (may be empty)."""
        cur = db_conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            cur.fetchone()
        except psycopg.errors.UndefinedTable:
            pytest.fail(f"Table '{table}' does not exist")
        finally:
            cur.close()


class TestForeignKeyIntegrity:
    """Verify FK relationships are intact after migration."""

    def test_stack_inputs_reference_valid_stacks(self, db_conn):
        """All stack_inputs.stack_id values exist in stacks."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM stack_inputs si
            WHERE NOT EXISTS (
                SELECT 1 FROM stacks s
                WHERE s.id = si.stack_id AND s.tenant_id = si.tenant_id
            )
        """)
        orphans = cur.fetchone()[0]
        cur.close()

        assert orphans == 0, (
            f"Found {orphans} stack_inputs rows with invalid stack_id FK"
        )

    def test_stack_inputs_reference_valid_health_inputs(self, db_conn):
        """All stack_inputs.health_input_id values exist in health_inputs."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM stack_inputs si
            WHERE NOT EXISTS (
                SELECT 1 FROM health_inputs hi
                WHERE hi.id = si.health_input_id AND hi.tenant_id = si.tenant_id
            )
        """)
        orphans = cur.fetchone()[0]
        cur.close()

        assert orphans == 0, (
            f"Found {orphans} stack_inputs rows with invalid health_input_id FK"
        )

    def test_health_input_log_reference_valid_inputs(self, db_conn):
        """All health_input_log.input_id values exist in health_inputs."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM health_input_log hil
            WHERE NOT EXISTS (
                SELECT 1 FROM health_inputs hi
                WHERE hi.id = hil.input_id AND hi.tenant_id = hil.tenant_id
            )
        """)
        orphans = cur.fetchone()[0]
        cur.close()

        assert orphans == 0, (
            f"Found {orphans} health_input_log rows with invalid input_id FK"
        )


class TestColumnMappings:
    """
    Verify column names match between related tables.

    This test class specifically catches the bug where v8 used 'input_id'
    but v10 expected 'health_input_id' in stack_inputs.
    """

    def test_stack_inputs_has_health_input_id(self, db_conn):
        """Verify stack_inputs has health_input_id column (not input_id)."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'stack_inputs' AND column_name = 'health_input_id'
        """)
        result = cur.fetchone()
        cur.close()

        assert result is not None, (
            "stack_inputs table missing 'health_input_id' column. "
            "Schema may be outdated or migration used wrong column name."
        )

    def test_no_null_health_input_ids(self, db_conn):
        """Verify no NULL values in required FK columns."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM stack_inputs WHERE health_input_id IS NULL
        """)
        nulls = cur.fetchone()[0]
        cur.close()

        assert nulls == 0, (
            f"Found {nulls} stack_inputs rows with NULL health_input_id. "
            "This was the exact symptom of the v8→v10 migration bug."
        )
