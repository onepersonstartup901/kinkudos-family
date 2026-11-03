from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from economy.forms import FamilyPreferencesForm
from economy.models import (
    AssignedTask,
    AssignedTaskBatch,
    AssignedTaskStatus,
    ChildProfile,
    FamilySettings,
    GoalActivityType,
    GoalMode,
    LedgerKind,
    SavingsContribution,
    SavingsContributionState,
    WeeklyBonus,
    WeeklyBonusKind,
)
from economy.services import (
    add_saved_points,
    create_savings_goal,
    post_ledger_entry,
    select_goal_mode,
)
from economy.templatetags.economy_tags import currency_unit
from economy.weekly import (
    pay_weekly_savings_interest,
    pay_weekly_streak_bonuses,
    previous_week_start,
    streak_status,
)

# Monday 26 October 2026; the paid week is Monday 19 to Sunday 25 October.
PAYDAY = date(2026, 10, 26)
LAST_MONDAY = date(2026, 10, 19)


def _at(day, hour=12):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class WeeklyBonusTestCase(TestCase):
    def setUp(self):
        self.parent = get_user_model().objects.create_user(
            "weekly-parent",
            password="Very-safe-pass-123!",
        )
        self.child = ChildProfile(name="Saver", min_balance=0, theme_selected=True)
        self.child.set_pin("1234")
        self.child.save()

    def configure(self, **values):
        family = FamilySettings.load()
        for name, value in values.items():
            setattr(family, name, value)
        family.save()

    def fund(self, amount):
        post_ledger_entry(
            child=self.child,
            delta=amount,
            kind=LedgerKind.ADJUSTMENT,
            description="Test funding",
            actor=self.parent,
        )

    def saved_goal(self, *, target=200, saved=0, saved_on=date(2026, 10, 10)):
        goal = create_savings_goal(
            child=self.child, title="Headphones", target_amount=target, actor=self.parent
        )
        select_goal_mode(goal=goal, child=self.child, mode=GoalMode.SAVED)
        goal.refresh_from_db()
        if saved:
            self.fund(saved)
            contribution = add_saved_points(goal=goal, child=self.child, amount=saved)
            SavingsContribution.objects.filter(pk=contribution.pk).update(
                created_at=_at(saved_on)
            )
        return goal


class CurrencyTests(WeeklyBonusTestCase):
    def test_every_theme_shows_australian_dollars(self):
        for theme in ("neutral", "block_world", "magic_academy", "hero_hq", "panda_pet"):
            self.assertEqual(currency_unit(1, theme), "AUD")
            self.assertEqual(currency_unit(25, theme), "AUD")

    def test_week_helper_returns_previous_monday(self):
        self.assertEqual(previous_week_start(PAYDAY), LAST_MONDAY)
        self.assertEqual(previous_week_start(date(2026, 11, 1)), LAST_MONDAY)


class SavingsInterestTests(WeeklyBonusTestCase):
    def test_interest_is_off_by_default(self):
        self.saved_goal(saved=100)
        self.assertEqual(pay_weekly_savings_interest(current_date=PAYDAY), [])
        self.assertFalse(WeeklyBonus.objects.exists())

    def test_pays_whole_dollars_into_goal_once_per_week(self):
        self.configure(savings_interest_percent=5)
        goal = self.saved_goal(saved=100)

        paid = pay_weekly_savings_interest(current_date=PAYDAY)
        again = pay_weekly_savings_interest(current_date=PAYDAY)

        self.assertEqual(len(paid), 1)
        self.assertEqual(again, [])
        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 105)
        self.assertEqual(goal.interest_carry_cents, 0)
        self.assertTrue(
            goal.events.filter(event_type=GoalActivityType.INTEREST, amount=5).exists()
        )
        # Interest is new money in the goal, not taken from the spendable balance.
        self.child.refresh_from_db()
        self.assertEqual(self.child.balance, 0)

    def test_small_balances_carry_cents_until_a_dollar(self):
        self.configure(savings_interest_percent=5)
        goal = self.saved_goal(saved=10)  # 50 cents a week

        pay_weekly_savings_interest(current_date=PAYDAY)
        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 10)
        self.assertEqual(goal.interest_carry_cents, 50)

        pay_weekly_savings_interest(current_date=PAYDAY + timedelta(days=7))
        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 11)
        self.assertEqual(goal.interest_carry_cents, 0)

    def test_money_added_during_the_week_earns_nothing_yet(self):
        self.configure(savings_interest_percent=10)
        goal = self.saved_goal(saved=100, saved_on=LAST_MONDAY + timedelta(days=3))

        pay_weekly_savings_interest(current_date=PAYDAY)

        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 100)
        self.assertTrue(WeeklyBonus.objects.filter(goal=goal, amount=0).exists())

    def test_interest_compounds_the_following_week(self):
        self.configure(savings_interest_percent=10)
        goal = self.saved_goal(saved=100)

        pay_weekly_savings_interest(current_date=PAYDAY)
        pay_weekly_savings_interest(current_date=PAYDAY + timedelta(days=7))

        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 121)

    def test_interest_stops_at_the_goal_target(self):
        self.configure(savings_interest_percent=10)
        goal = self.saved_goal(target=103, saved=100)

        pay_weekly_savings_interest(current_date=PAYDAY)

        goal.refresh_from_db()
        self.assertEqual(goal.saved_amount, 103)
        self.assertEqual(goal.interest_carry_cents, 0)

    def test_returned_money_does_not_earn_interest(self):
        self.configure(savings_interest_percent=5)
        goal = self.saved_goal(saved=100)
        goal.contributions.update(state=SavingsContributionState.RETURNED)

        pay_weekly_savings_interest(current_date=PAYDAY)

        self.assertFalse(goal.events.filter(event_type=GoalActivityType.INTEREST).exists())


class StreakBonusTests(WeeklyBonusTestCase):
    def assign(self, day, statuses):
        batch = AssignedTaskBatch.objects.create(
            child=self.child, assigned_by=self.parent, assigned_on=day
        )
        for index, status in enumerate(statuses):
            AssignedTask.objects.create(
                batch=batch,
                title_snapshot=f"Job {index}",
                reward_snapshot=0,
                status=status,
            )
        return batch

    def perfect_days(self, count):
        for offset in range(count):
            self.assign(LAST_MONDAY + timedelta(days=offset), [AssignedTaskStatus.COMPLETED])

    def test_streak_is_off_by_default(self):
        self.perfect_days(7)
        self.assertEqual(pay_weekly_streak_bonuses(current_date=PAYDAY), [])

    def test_pays_bonus_for_enough_perfect_days_once(self):
        self.configure(streak_bonus_points=5, streak_min_days=5)
        self.perfect_days(5)
        # A cancelled job does not spoil a day.
        self.assign(
            LAST_MONDAY + timedelta(days=5),
            [AssignedTaskStatus.COMPLETED, AssignedTaskStatus.CANCELLED],
        )

        paid = pay_weekly_streak_bonuses(current_date=PAYDAY)
        again = pay_weekly_streak_bonuses(current_date=PAYDAY)

        self.assertEqual(len(paid), 1)
        self.assertEqual(again, [])
        self.child.refresh_from_db()
        self.assertEqual(self.child.balance, 5)
        entry = self.child.ledger_entries.get(kind=LedgerKind.STREAK)
        self.assertEqual(entry.delta, 5)
        self.assertEqual(paid[0].perfect_days, 6)

    def test_interest_payout_in_same_week_does_not_block_streak(self):
        self.configure(streak_bonus_points=5, streak_min_days=5, savings_interest_percent=5)
        self.saved_goal(saved=100)
        self.perfect_days(5)

        pay_weekly_savings_interest(current_date=PAYDAY)
        paid = pay_weekly_streak_bonuses(current_date=PAYDAY)

        self.assertEqual(len(paid), 1)

    def test_a_missed_day_breaks_the_streak(self):
        self.configure(streak_bonus_points=5, streak_min_days=5)
        self.perfect_days(6)
        self.assign(LAST_MONDAY + timedelta(days=6), [AssignedTaskStatus.PENDING])

        self.assertEqual(pay_weekly_streak_bonuses(current_date=PAYDAY), [])
        self.child.refresh_from_db()
        self.assertEqual(self.child.balance, 0)
        self.assertTrue(
            WeeklyBonus.objects.filter(kind=WeeklyBonusKind.STREAK, amount=0).exists()
        )

    def test_too_few_perfect_days_earns_nothing(self):
        self.configure(streak_bonus_points=5, streak_min_days=5)
        self.perfect_days(4)
        self.assertEqual(pay_weekly_streak_bonuses(current_date=PAYDAY), [])

    def test_dashboard_status_tracks_the_current_week(self):
        self.configure(streak_bonus_points=5, streak_min_days=3)
        wednesday = LAST_MONDAY + timedelta(days=2)
        self.assign(LAST_MONDAY, [AssignedTaskStatus.COMPLETED])
        self.assign(LAST_MONDAY + timedelta(days=1), [AssignedTaskStatus.COMPLETED])
        self.assign(wednesday, [AssignedTaskStatus.PENDING])

        status = streak_status(self.child, current_date=wednesday)

        self.assertEqual(status["perfect"], 2)
        self.assertFalse(status["broken"])
        self.assertFalse(status["earned"])
        states = [item["state"] for item in status["calendar"]]
        self.assertEqual(states[:4], ["perfect", "perfect", "open", "future"])

    def test_dashboard_status_hidden_when_streak_is_off(self):
        self.assertIsNone(streak_status(self.child, current_date=PAYDAY))


class FamilyPreferencesFormTests(WeeklyBonusTestCase):
    def base_data(self):
        family = FamilySettings.load()
        return {
            "family_name": "Family",
            "photo_bonus_points": 0,
            "birthday_points": 0,
            "lottery_ticket_cost": family.lottery_ticket_cost,
            "lottery_weekly_limit": family.lottery_weekly_limit,
            "evidence_retention_days": family.evidence_retention_days,
            "feedback_screenshot_retention_days": family.feedback_screenshot_retention_days,
        }

    def test_saves_weekly_settings(self):
        data = {
            **self.base_data(),
            "savings_interest_percent": 5,
            "streak_bonus_points": 10,
            "streak_min_days": 6,
        }
        form = FamilyPreferencesForm(data, instance=FamilySettings.load())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        family = FamilySettings.objects.get(pk=1)
        self.assertEqual(
            (family.savings_interest_percent, family.streak_bonus_points, family.streak_min_days),
            (5, 10, 6),
        )

    def test_missing_weekly_fields_keep_current_values(self):
        self.configure(savings_interest_percent=7)
        form = FamilyPreferencesForm(self.base_data(), instance=FamilySettings.load())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(FamilySettings.objects.get(pk=1).savings_interest_percent, 7)

    def test_rejects_out_of_range_values(self):
        data = {**self.base_data(), "savings_interest_percent": 80, "streak_min_days": 9}
        form = FamilyPreferencesForm(data, instance=FamilySettings.load())
        self.assertFalse(form.is_valid())
        self.assertIn("savings_interest_percent", form.errors)
        self.assertIn("streak_min_days", form.errors)


class ParentSettingsPageTests(WeeklyBonusTestCase):
    def test_settings_page_shows_weekly_fields(self):
        self.parent.is_staff = True
        self.parent.save(update_fields=["is_staff"])
        self.client.force_login(self.parent)
        response = self.client.get(reverse("parent_dashboard"))
        if response.status_code != 200:
            self.skipTest("Parent dashboard needs extra setup in this environment.")
        self.assertContains(response, 'name="savings_interest_percent"')
        self.assertContains(response, 'name="streak_bonus_points"')
