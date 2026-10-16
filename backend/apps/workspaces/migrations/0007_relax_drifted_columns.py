"""
Repair schema drift left by migrations from another branch.

Production once ran the `crisp` branch's migrations (e.g.
workspaces.0006_workspace_away_message_workspace_office_hours_and_more), which
added NOT NULL columns without database defaults (away_message, timezone,
offline_form_enabled, ...). This codebase does not know those columns, so every
Workspace insert failed with MySQL error 1364 and sign-up was impossible.

Make every such unknown, NOT NULL, default-less column nullable. Nothing is
dropped, so existing values survive (and that branch can still be merged later).
On a database without drift this is a no-op; it only runs on MySQL.
"""
from django.db import migrations

APP_LABELS = ('accounts', 'billing', 'chat', 'employees', 'knowledge', 'leads', 'workspaces')


def relax(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    qn = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        for label in APP_LABELS:
            for model in apps.get_app_config(label).get_models():
                table = model._meta.db_table
                known = {f.column for f in model._meta.concrete_fields}
                cursor.execute(
                    'SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS '
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND IS_NULLABLE = 'NO' "
                    "AND COLUMN_DEFAULT IS NULL AND EXTRA NOT LIKE '%%auto_increment%%'",
                    [table],
                )
                for column, column_type in cursor.fetchall():
                    if column in known:
                        continue
                    schema_editor.execute(f'ALTER TABLE {qn(table)} MODIFY {qn(column)} {column_type} NULL')


class Migration(migrations.Migration):
    # Depend on every app's latest migration so the model state lists all
    # columns this codebase knows about.
    dependencies = [
        ('accounts', '0007_unverify_backfilled_unused_accounts'),
        ('billing', '0004_transaction'),
        ('chat', '0004_chatsession_is_test'),
        ('employees', '0002_aiemployee_capabilities'),
        ('knowledge', '0002_is_public'),
        ('leads', '0004_lead_status_notes'),
        ('workspaces', '0006_workspace_invite'),
    ]

    operations = [migrations.RunPython(relax, migrations.RunPython.noop)]
