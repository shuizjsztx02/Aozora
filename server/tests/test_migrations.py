"""T-M2-02: Tests for migration consistency.

Verifies that the Alembic migration DDL and SQLAlchemy ORM models
define the same set of tables and columns — i.e. the migration is
the authoritative DDL source and the models stay in sync.
"""

from __future__ import annotations

import importlib

from sqlalchemy import MetaData

from src.models.base import Base


class TestMigrationConsistency:
    """Ensure migration DDL matches ORM metadata."""

    def test_all_tables_present_in_migration(self):
        """Every ORM table must appear in at least one migration."""
        import importlib
        import inspect
        import re

        # Collect all migration upgrade() sources
        migrations = []
        for rev in ("0001_initial", "0002_exports", "0003_sync_idempotency"):
            try:
                m = importlib.import_module(f"src.migrations.versions.{rev}")
                migrations.append(m)
            except ImportError:
                pass

        orm_tables = set(Base.metadata.tables.keys())

        # Aggregate all create_table calls across migrations
        all_created = set()
        for m in migrations:
            if hasattr(m, "upgrade"):
                source = inspect.getsource(m.upgrade)
                all_created.update(re.findall(r'op\.create_table\(\s*"(\w+)"', source))

        for table_name in orm_tables:
            assert table_name in all_created, (
                f"Table '{table_name}' is in ORM models but not in any migration"
            )

    def test_no_orphan_tables_in_migration(self):
        """Migration should not define tables absent from ORM models."""
        import importlib
        import inspect
        import re

        orm_tables = set(Base.metadata.tables.keys())

        # Aggregate all create_table calls across migrations
        all_created = set()
        for rev in ("0001_initial", "0002_exports", "0003_sync_idempotency"):
            try:
                m = importlib.import_module(f"src.migrations.versions.{rev}")
                if hasattr(m, "upgrade"):
                    source = inspect.getsource(m.upgrade)
                    all_created.update(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
            except ImportError:
                pass

        orphans = all_created - orm_tables
        assert not orphans, f"Migration creates tables not in ORM: {orphans}"

    def test_amount_columns_are_integer(self):
        """All amount_cents fields must use BigInteger — never Float/Numeric."""
        from sqlalchemy import BigInteger

        amount_columns = []
        for table in Base.metadata.tables.values():
            for col in table.columns:
                if "amount" in col.name:
                    amount_columns.append((table.name, col.name, type(col.type)))

        assert len(amount_columns) >= 2, "Expected at least 2 amount columns"
        for table_name, col_name, col_type in amount_columns:
            assert col_type is BigInteger, (
                f"{table_name}.{col_name} uses {col_type.__name__}, expected BigInteger"
            )

    def test_version_columns_present(self):
        """Entities that require versioning must have a version column."""
        versioned_tables = {
            "books", "categories", "accounts", "transactions", "budgets",
        }
        for table_name in versioned_tables:
            table = Base.metadata.tables[table_name]
            assert "version" in table.columns, (
                f"{table_name} missing 'version' column"
            )

    def test_soft_delete_columns_present(self):
        """Soft-deletable entities must have a deleted_at column."""
        # T-M3-02: budgets joined the soft-delete set (migration 0006) so
        # sync deletes are durable instead of silently lost.
        soft_delete_tables = {"books", "transactions", "budgets"}
        for table_name in soft_delete_tables:
            table = Base.metadata.tables[table_name]
            assert "deleted_at" in table.columns, (
                f"{table_name} missing 'deleted_at' column"
            )

    def test_check_constraints_on_amounts(self):
        """amount_cents columns must have > 0 check constraints."""
        for table_name in ("transactions", "budgets"):
            table = Base.metadata.tables[table_name]
            checks = [
                c for c in table.constraints
                if hasattr(c, "sqltext") and "amount_cents" in str(c.sqltext)
            ]
            assert len(checks) > 0, (
                f"{table_name} missing CHECK constraint on amount_cents"
            )

    def test_13_tables_registered(self):
        """The schema must define exactly 13 tables."""
        assert len(Base.metadata.tables) == 13

    def test_transaction_note_column_bounded(self):
        """N-13: transactions.note must be bounded at the DB layer too.

        The Pydantic schema caps notes at 2000 chars; the column must
        match so a writer that bypasses the API is still bounded.
        """
        from sqlalchemy import String

        column = Base.metadata.tables["transactions"].columns["note"]
        assert isinstance(column.type, String)
        assert column.type.length == 2000

    def test_note_length_migration_chains_onto_0004(self):
        """N-13: 0005_note_length must extend the migration chain."""
        m4 = importlib.import_module("src.migrations.versions.0004_budget_unique_constraint")
        m5 = importlib.import_module("src.migrations.versions.0005_note_length")
        assert m5.down_revision == m4.revision
        assert m5.revision == "0005_note_length"

    def test_budget_deleted_at_migration_chains_onto_0005(self):
        """T-M3-02: 0006_budget_deleted_at must extend the migration chain."""
        m5 = importlib.import_module("src.migrations.versions.0005_note_length")
        m6 = importlib.import_module("src.migrations.versions.0006_budget_deleted_at")
        assert m6.down_revision == m5.revision
        assert m6.revision == "0006_budget_deleted_at"
