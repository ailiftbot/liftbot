import importlib
import unittest

from django.apps import apps as global_apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from apps.billing.models import BillingPlan

from .models import Workspace

migration = importlib.import_module('apps.workspaces.migrations.0007_relax_drifted_columns')


@unittest.skipUnless(connection.vendor == 'mysql', 'Schema drift repair is MySQL-only')
class RelaxDriftedColumnsTests(TransactionTestCase):
    """Production had extra NOT NULL columns from another branch; inserts failed with error 1364."""

    def setUp(self):
        with connection.cursor() as c:
            c.execute('ALTER TABLE workspaces_workspace ADD COLUMN away_message longtext NOT NULL')
            c.execute("ALTER TABLE workspaces_workspace ADD COLUMN `timezone` varchar(64) NOT NULL")
        self.addCleanup(self._drop_columns)

    def _drop_columns(self):
        with connection.cursor() as c:
            c.execute('ALTER TABLE workspaces_workspace DROP COLUMN away_message, DROP COLUMN `timezone`')

    def test_migration_is_not_atomic(self):
        # DDL inside the migration transaction raises TransactionManagementError on MySQL.
        self.assertFalse(migration.Migration.atomic)

    def test_migrate_command_applies_repair(self):
        from django.core.management import call_command
        from django.db.migrations.recorder import MigrationRecorder

        # Forget 0007 and everything after it, or Django flags the history as inconsistent.
        MigrationRecorder.Migration.objects.filter(app='workspaces', name__gte='0007').delete()
        call_command('migrate', 'workspaces', verbosity=0)
        owner = User.objects.create_user('m@example.com', 'm@example.com', 'pw-123456!')
        Workspace.objects.create(name='Acme', owner=owner)

    def test_workspace_insert_works_after_repair(self):
        owner = User.objects.create_user('o@example.com', 'o@example.com', 'pw-123456!')
        plan = BillingPlan.objects.create(
            slug='starter', name='Starter', price_monthly=19, conversation_limit=500, token_limit=200_000,
        )
        with connection.schema_editor() as editor:
            migration.relax(global_apps, editor)
        ws = Workspace.objects.create(name='Acme', owner=owner, plan=plan)
        self.assertTrue(Workspace.objects.filter(pk=ws.pk).exists())
        with connection.cursor() as c:
            c.execute(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'workspaces_workspace' AND COLUMN_NAME = 'is_active'"
            )
            self.assertEqual(c.fetchone()[0], 'NO', 'known columns must keep NOT NULL')
