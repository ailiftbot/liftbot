from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0002_workspace_widget_token'),
        ('employees', '0002_aiemployee_capabilities'),
        ('leads', '0002_lead_intent_source'),
        ('chat', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='status',
            field=models.CharField(
                choices=[('active', 'Active'), ('paused', 'Paused'), ('closed', 'Closed')],
                default='active',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='VisitorProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('visitor_id', models.CharField(db_index=True, max_length=64)),
                ('name', models.CharField(blank=True, max_length=150)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('preferences', models.JSONField(blank=True, default=dict)),
                ('conversation_summary', models.TextField(blank=True)),
                ('last_seen_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='visitor_profiles', to='chat.chatsession')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='visitors', to='workspaces.workspace')),
            ],
            options={
                'ordering': ('-last_seen_at',),
                'unique_together': {('workspace', 'visitor_id')},
            },
        ),
        migrations.CreateModel(
            name='EmployeeTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_type', models.CharField(choices=[('qualify', 'Qualify visitor'), ('handoff', 'Team handoff'), ('schedule', 'Schedule / visit'), ('follow_up', 'Follow up')], max_length=20)),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In progress'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='open', max_length=20)),
                ('title', models.CharField(max_length=255)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('notified_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='employees.aiemployee')),
                ('lead', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='leads.lead')),
                ('session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='chat.chatsession')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='employee_tasks', to='workspaces.workspace')),
            ],
            options={
                'ordering': ('-created_at',),
            },
        ),
    ]
