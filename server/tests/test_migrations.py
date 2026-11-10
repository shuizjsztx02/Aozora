"""T-M2-02: Tests for migration consistency.

Verifies that the Alembic migration DDL and SQLAlchemy ORM models
define the same set of tables and columns — i.e. the migration is
the authoritative DDL source and the models stay in sync.
"""

from __future__ import annotations

import importlib

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
        for rev in (
            "0001_initial", "0002_exports", "0003_sync_idempotency",
            "0004_budget_unique_constraint", "0005_note_length",
            "0006_budget_deleted_at", "0007_ai_usage_index",
            "0008_proposal_idempotency", "0009_attachment_user_id",
            "0010_attachment_user_id_backfill", "0011_user_consents",
            "0012_account_deletion", "0013_crash_reports",
            "0014_deletion_token_unique", "0015_feedback",
            "0016_cloud_entities", "0017_conversation_events",
            "0018_conversation_event_turns",
            "0019_conversation_index",
            "0020_conversation_event_log",
            "0021_user_preferences",
            "0022_account_balance_type",
            "0023_category_icon",
            "0024_m8_admin_console",
        ):
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
        for rev in (
            "0001_initial", "0002_exports", "0003_sync_idempotency",
            "0004_budget_unique_constraint", "0005_note_length",
            "0006_budget_deleted_at", "0007_ai_usage_index",
            "0008_proposal_idempotency", "0009_attachment_user_id",
            "0010_attachment_user_id_backfill", "0011_user_consents",
            "0012_account_deletion", "0013_crash_reports",
            "0014_deletion_token_unique", "0015_feedback",
            "0016_cloud_entities", "0017_conversation_events",
            "0018_conversation_event_turns",
            "0019_conversation_index",
            "0020_conversation_event_log",
            "0021_user_preferences",
            "0022_account_balance_type",
            "0023_category_icon",
            "0024_m8_admin_console",
        ):
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

    def test_23_tables_registered(self):
        """The schema must define exactly 24 tables.

        T-M4-02b added ``proposal_idempotency`` (migration 0008) for AI
        execute dedup, bringing the total from 13 to 14.
        T-M5-04a added ``user_consents`` (migration 0011), bringing 14 → 15.
        T-M5-04c added ``deletion_tokens`` + ``deletion_audit`` (migration 0012),
        bringing 15 → 17.
        T-M5-05b added ``crash_reports`` (migration 0013), 17 → 18.
        T-M5-05c added ``feedbacks`` (migration 0015), 18 → 19.
        T-M7-01 added ``savings_goals`` + ``scheduled_rules`` + ``chat_personas``
        (migration 0016), 19 → 22 — the three client-only entities that became
        cloud-authoritative.
        T-M7-02f added ``ai_conversation_events`` (migration 0017), 22 → 23 —
        the minimal conversation event log backing multi-turn context.
        T-M7-05j added ``user_preferences`` (migration 0021), 23 → 24 — the
        user-level preference keys (default_book / default_account / widget /
        profile) that migrate from device-local ``sync_meta`` to cloud.
        M8-03 added ``admin_accounts`` + ``admin_audit_log`` + ``ai_proposal_links``
        (migration 0024), 24 → 27 — the admin console's identity, audit and
        proposal→conversation link.
        """
        assert len(Base.metadata.tables) == 27

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

    def test_ai_usage_index_migration_chains_onto_0006(self):
        """T-M4-01: 0007_ai_usage_index must extend the migration chain."""
        m6 = importlib.import_module("src.migrations.versions.0006_budget_deleted_at")
        m7 = importlib.import_module("src.migrations.versions.0007_ai_usage_index")
        assert m7.down_revision == m6.revision
        assert m7.revision == "0007_ai_usage_index"

    def test_proposal_idempotency_migration_chains_onto_0007(self):
        """T-M4-02b: 0008_proposal_idempotency must extend the migration chain."""
        m7 = importlib.import_module("src.migrations.versions.0007_ai_usage_index")
        m8 = importlib.import_module("src.migrations.versions.0008_proposal_idempotency")
        assert m8.down_revision == m7.revision
        assert m8.revision == "0008_proposal_idempotency"

    def test_attachment_user_id_migration_chains_onto_0008(self):
        """T-M4-04c: 0009_attachment_user_id must extend the migration chain."""
        m8 = importlib.import_module("src.migrations.versions.0008_proposal_idempotency")
        m9 = importlib.import_module("src.migrations.versions.0009_attachment_user_id")
        assert m9.down_revision == m8.revision
        assert m9.revision == "0009_attachment_user_id"

    def test_attachment_backfill_migration_chains_onto_0009(self):
        """NEW-1/NEW-2: 0010_attachment_user_id_backfill must extend the chain."""
        m9 = importlib.import_module("src.migrations.versions.0009_attachment_user_id")
        m10 = importlib.import_module("src.migrations.versions.0010_attachment_user_id_backfill")
        assert m10.down_revision == m9.revision
        assert m10.revision == "0010_attachment_user_id_backfill"

    def test_cloud_entities_migration_chains_onto_0015(self):
        """T-M7-01: 0016_cloud_entities must extend the migration chain."""
        m15 = importlib.import_module("src.migrations.versions.0015_feedback")
        m16 = importlib.import_module("src.migrations.versions.0016_cloud_entities")
        assert m16.down_revision == m15.revision
        assert m16.revision == "0016_cloud_entities"

    def test_conversation_events_migration_chains_onto_0016(self):
        """T-M7-02f: 0017_conversation_events must extend the migration chain."""
        m16 = importlib.import_module("src.migrations.versions.0016_cloud_entities")
        m17 = importlib.import_module("src.migrations.versions.0017_conversation_events")
        assert m17.down_revision == m16.revision
        assert m17.revision == "0017_conversation_events"

    def test_conversation_event_turns_migration_chains_onto_0017(self):
        """T-M7-02f 复审 P2-1: 0018 must extend the chain (窗口按 turn 整取)."""
        m17 = importlib.import_module("src.migrations.versions.0017_conversation_events")
        m18 = importlib.import_module("src.migrations.versions.0018_conversation_event_turns")
        assert m18.down_revision == m17.revision
        assert m18.revision == "0018_conversation_event_turns"

    def test_conversation_index_migration_chains_onto_0018(self):
        """T-M7-03a: 0019 must extend the chain (会话可列表：title / 活跃 / 软删)."""
        m18 = importlib.import_module("src.migrations.versions.0018_conversation_event_turns")
        m19 = importlib.import_module("src.migrations.versions.0019_conversation_index")
        assert m19.down_revision == m18.revision
        assert m19.revision == "0019_conversation_index"

    def test_conversation_index_migration_adds_the_listing_columns(self):
        """0019 的三列与列表索引必须在迁移里真的加上。

        SQLite 测试面走 ``Base.metadata.create_all``、**不经本迁移**，所以「ORM
        改了、迁移忘了」在测试里完全看不出来——这条把那段差距钉住（列名与索引名
        都在 ``AIConversation`` 上声明，两边同一个源头）。
        """
        import inspect

        from src.models.ai import AIConversation

        m19 = importlib.import_module("src.migrations.versions.0019_conversation_index")
        source = inspect.getsource(m19.upgrade)

        for column in ("title", "last_active_at", "archived_at"):
            assert column in AIConversation.__table__.columns, f"ORM 缺列 {column}"
            assert f'"{column}"' in source, f"0019 没加列 {column}"

        index_name = "ix_ai_conversations_user_archived_active"
        assert index_name in {i.name for i in AIConversation.__table__.indexes}
        assert index_name in source

    def test_account_balance_type_migration_chains_onto_0021(self):
        """REQ-69（T18-5）: 0022 must extend the chain（账户分类与当前余额两端化）."""
        m21 = importlib.import_module("src.migrations.versions.0021_user_preferences")
        m22 = importlib.import_module("src.migrations.versions.0022_account_balance_type")
        assert m22.down_revision == m21.revision
        assert m22.revision == "0022_account_balance_type"

    def test_account_balance_type_migration_adds_the_columns(self):
        """0022 的两列必须在迁移里真的加上。

        SQLite 测试面走 ``Base.metadata.create_all``、**不经本迁移**，所以
        「ORM 改了、迁移忘了」在测试里完全看不出来 —— 这条把那段差距钉住。
        """
        import inspect

        from sqlalchemy import BigInteger

        from src.models.ledger import Account

        m22 = importlib.import_module("src.migrations.versions.0022_account_balance_type")
        source = inspect.getsource(m22.upgrade)

        for column in ("account_type", "balance_cents"):
            assert column in Account.__table__.columns, f"ORM 缺列 {column}"
            assert f'"{column}"' in source, f"0022 没加列 {column}"

        # 金额列一律 BigInteger（同 test_amount_columns_are_integer 的口径）。
        assert isinstance(Account.__table__.columns["balance_cents"].type, BigInteger)

        # 负余额（信用卡欠款）必须能存：不带非负 CHECK，与 savings_goals 刻意不同。
        checks = [
            str(c.sqltext) for c in Account.__table__.constraints
            if hasattr(c, "sqltext")
        ]
        assert not any("balance_cents" in c for c in checks)
