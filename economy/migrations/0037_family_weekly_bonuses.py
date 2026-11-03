# Family edition: weekly savings interest and perfect-week streak bonus.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('economy', '0036_caregiver_guest_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='familysettings',
            name='savings_interest_percent',
            field=models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MaxValueValidator(50)]),
        ),
        migrations.AddField(
            model_name='familysettings',
            name='streak_bonus_points',
            field=models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MaxValueValidator(10000)]),
        ),
        migrations.AddField(
            model_name='familysettings',
            name='streak_min_days',
            field=models.PositiveSmallIntegerField(default=5, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)]),
        ),
        migrations.AddField(
            model_name='savingsgoal',
            name='interest_carry_cents',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='ledgerentry',
            name='kind',
            field=models.CharField(choices=[('task', 'Task'), ('assigned_task', 'Assigned task'), ('penalty', 'Penalty'), ('reward', 'Reward'), ('lottery', 'Surprise card'), ('adjustment', 'Adjustment'), ('gift', 'Gift'), ('birthday', 'Birthday'), ('savings_transfer', 'Saved to goal'), ('savings_return', 'Returned from goal'), ('goal_completion', 'Goal completion'), ('streak_bonus', 'Streak bonus')], max_length=16),
        ),
        migrations.AlterField(
            model_name='savingsgoalevent',
            name='event_type',
            field=models.CharField(choices=[('created', 'Goal created'), ('mode_selected', 'Savings mode selected'), ('current_changed', 'Current goal changed'), ('transferred', 'Points saved to goal'), ('returned', 'Points returned from goal'), ('reached', 'Goal reached'), ('completed', 'Goal completed'), ('closed', 'Goal closed'), ('interest', 'Interest paid')], max_length=24),
        ),
        migrations.CreateModel(
            name='WeeklyBonus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('interest', 'Savings interest'), ('streak', 'Perfect-week streak')], max_length=16)),
                ('week_start', models.DateField()),
                ('amount', models.PositiveIntegerField(default=0)),
                ('rate_percent', models.PositiveSmallIntegerField(default=0)),
                ('perfect_days', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('child', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='weekly_bonuses', to='economy.childprofile')),
                ('goal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='weekly_bonuses', to='economy.savingsgoal')),
            ],
            options={
                'ordering': ['-week_start', '-pk'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('kind', 'interest')), fields=('goal', 'week_start'), name='one_interest_payout_per_goal_week'), models.UniqueConstraint(condition=models.Q(('kind', 'streak')), fields=('child', 'week_start'), name='one_streak_payout_per_child_week')],
            },
        ),
    ]
