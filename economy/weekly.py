"""Family edition weekly payouts: savings interest and the perfect-week streak.

Both payouts are for the *previous* Monday-to-Sunday week and are recorded in
WeeklyBonus so that running the scheduler many times never pays twice.
"""

from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    AssignedTask,
    AssignedTaskStatus,
    ChildProfile,
    FamilySettings,
    GoalActivityType,
    GoalMode,
    GoalStatus,
    LedgerKind,
    SavingsContribution,
    SavingsContributionState,
    SavingsGoal,
    WeeklyBonus,
    WeeklyBonusKind,
)


def week_start_for(day):
    """Return the Monday of the week that contains day."""
    return day - timedelta(days=day.weekday())


def previous_week_start(current_date=None):
    current_date = current_date or timezone.localdate()
    return week_start_for(current_date) - timedelta(days=7)


def _local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


# --------------------------------------------------------------------------
# Savings interest
# --------------------------------------------------------------------------


def interest_base_amount(goal, week_start):
    """Money that sat in the goal for the whole week.

    Only contributions made before the week started and still saved now count,
    so moving money in on Sunday night and out on Monday earns nothing.
    """
    return int(
        goal.contributions.filter(
            state=SavingsContributionState.ACTIVE,
            created_at__lt=_local_midnight(week_start),
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )


def pay_weekly_savings_interest(*, current_date=None):
    """Pay last week's interest into every active "save separately" goal."""
    rate = FamilySettings.load().savings_interest_percent
    if rate <= 0:
        return []

    from .services import _active_saved_amount, _record_goal_event

    week_start = previous_week_start(current_date)
    paid_week_end = week_start + timedelta(days=7)
    goal_ids = list(
        SavingsGoal.objects.filter(
            status=GoalStatus.ACTIVE,
            mode=GoalMode.SAVED,
            child__is_active=True,
        )
        .exclude(
            pk__in=WeeklyBonus.objects.filter(
                kind=WeeklyBonusKind.INTEREST, week_start=week_start
            ).values("goal_id")
        )
        .values_list("pk", flat=True)
    )
    payouts = []
    for goal_id in goal_ids:
        try:
            with transaction.atomic():
                goal = SavingsGoal.objects.select_for_update().select_related("child").get(pk=goal_id)
                if goal.status != GoalStatus.ACTIVE or goal.mode != GoalMode.SAVED:
                    continue
                base = interest_base_amount(goal, week_start)
                # Dollars x percent = cents. Carry anything under $1 forward.
                total_cents = base * rate + goal.interest_carry_cents
                whole_dollars = total_cents // 100
                remaining = max(goal.target_amount - _active_saved_amount(goal), 0)
                amount = min(whole_dollars, remaining)
                if amount < whole_dollars or remaining == 0:
                    carry = 0  # goal is full; nothing more to grow
                else:
                    carry = total_cents - amount * 100
                payout = WeeklyBonus.objects.create(
                    child=goal.child,
                    goal=goal,
                    kind=WeeklyBonusKind.INTEREST,
                    week_start=week_start,
                    amount=amount,
                    rate_percent=rate,
                )
                if goal.interest_carry_cents != carry:
                    goal.interest_carry_cents = carry
                    goal.save(update_fields=["interest_carry_cents"])
                if amount > 0:
                    contribution = SavingsContribution.objects.create(
                        goal=goal,
                        amount=amount,
                        state=SavingsContributionState.ACTIVE,
                    )
                    # Date the interest at the end of the paid week so it
                    # counts towards next week's interest (compounding).
                    SavingsContribution.objects.filter(pk=contribution.pk).update(
                        created_at=_local_midnight(paid_week_end) - timedelta(seconds=1)
                    )
                    _record_goal_event(
                        goal=goal,
                        event_type=GoalActivityType.INTEREST,
                        description=_("Interest: %(amount)s AUD at %(rate)s%% for %(title)s")
                        % {"amount": amount, "rate": rate, "title": goal.title},
                        amount=amount,
                    )
                    payouts.append(payout)
        except IntegrityError:
            continue
    for payout in payouts:
        _notify_interest(payout)
    return payouts


def goal_interest_summary(goal):
    """Totals shown on a savings goal card."""
    earned = int(
        goal.weekly_bonuses.filter(kind=WeeklyBonusKind.INTEREST).aggregate(
            total=Sum("amount")
        )["total"]
        or 0
    )
    return {
        "earned": earned,
        "carry_cents": goal.interest_carry_cents,
    }


# --------------------------------------------------------------------------
# Perfect-week streak
# --------------------------------------------------------------------------


def week_job_days(child, week_start, *, through=None, today=None):
    """Classify each day of a week by the child's assigned (expected) jobs.

    Returns {"perfect": [dates], "missed": [dates], "open": [dates]}.
    A day is perfect when it had at least one job and every job that was not
    cancelled got done. A past day with an unfinished job is missed. Today's
    unfinished jobs are still open. Days without jobs do not count either way.
    """
    through = through or (week_start + timedelta(days=6))
    today = today or timezone.localdate()
    items = AssignedTask.objects.filter(
        batch__child=child,
        batch__assigned_on__gte=week_start,
        batch__assigned_on__lte=through,
    ).exclude(status=AssignedTaskStatus.CANCELLED)
    by_day = {}
    for assigned_on, status in items.values_list("batch__assigned_on", "status"):
        by_day.setdefault(assigned_on, []).append(status)
    result = {"perfect": [], "missed": [], "open": []}
    for day in sorted(by_day):
        statuses = by_day[day]
        if all(status == AssignedTaskStatus.COMPLETED for status in statuses):
            result["perfect"].append(day)
        elif day >= today:
            result["open"].append(day)
        else:
            result["missed"].append(day)
    return result


def pay_weekly_streak_bonuses(*, current_date=None):
    """Pay the streak bonus for last week to every child who earned it."""
    family = FamilySettings.load()
    bonus = family.streak_bonus_points
    if bonus <= 0:
        return []

    from .services import post_ledger_entry

    current_date = current_date or timezone.localdate()
    week_start = previous_week_start(current_date)
    child_ids = list(
        ChildProfile.objects.filter(is_active=True)
        .exclude(
            pk__in=WeeklyBonus.objects.filter(
                kind=WeeklyBonusKind.STREAK, week_start=week_start
            ).values("child_id")
        )
        .values_list("pk", flat=True)
    )
    payouts = []
    for child_id in child_ids:
        try:
            with transaction.atomic():
                child = ChildProfile.objects.select_for_update().get(pk=child_id)
                days = week_job_days(child, week_start, today=current_date)
                perfect = len(days["perfect"])
                earned = perfect >= family.streak_min_days and not days["missed"]
                payout = WeeklyBonus.objects.create(
                    child=child,
                    kind=WeeklyBonusKind.STREAK,
                    week_start=week_start,
                    amount=bonus if earned else 0,
                    perfect_days=perfect,
                )
                if earned:
                    post_ledger_entry(
                        child=child,
                        delta=bonus,
                        kind=LedgerKind.STREAK,
                        description=_("Perfect week bonus: all jobs done on %(days)s days")
                        % {"days": perfect},
                        source_id=payout.pk,
                    )
                    payouts.append(payout)
        except IntegrityError:
            continue
    for payout in payouts:
        _notify_streak(payout)
    return payouts


def streak_status(child, *, current_date=None):
    """This week's streak progress for the child dashboard."""
    family = FamilySettings.load()
    if family.streak_bonus_points <= 0:
        return None
    current_date = current_date or timezone.localdate()
    week_start = week_start_for(current_date)
    days = week_job_days(child, week_start, through=current_date, today=current_date)
    perfect_days = set(days["perfect"])
    missed_days = set(days["missed"])
    open_days = set(days["open"])
    calendar = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if day in perfect_days:
            state = "perfect"
        elif day in missed_days:
            state = "missed"
        elif day in open_days:
            state = "open"
        elif day > current_date:
            state = "future"
        else:
            state = "none"
        calendar.append({"day": day, "state": state, "is_today": day == current_date})
    last_week = (
        WeeklyBonus.objects.filter(
            child=child,
            kind=WeeklyBonusKind.STREAK,
            week_start=week_start - timedelta(days=7),
        )
        .only("amount")
        .first()
    )
    return {
        "bonus": family.streak_bonus_points,
        "min_days": family.streak_min_days,
        "perfect": len(perfect_days),
        "broken": bool(missed_days),
        "earned": len(perfect_days) >= family.streak_min_days and not missed_days,
        "calendar": calendar,
        "last_week_paid": last_week.amount if last_week else None,
    }


def run_weekly_payouts(*, current_date=None):
    return {
        "interest": pay_weekly_savings_interest(current_date=current_date),
        "streak": pay_weekly_streak_bonuses(current_date=current_date),
    }


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------


def _notify_interest(payout):
    from .push import _child_dashboard_url, _child_subscriptions, _send

    _send(
        {
            "title": _("Your savings earned interest"),
            "body": _("%(title)s grew by %(amount)s AUD this week.")
            % {"title": payout.goal.title, "amount": payout.amount},
            "url": _child_dashboard_url("tikslai"),
            "tag": f"interest-{payout.pk}",
        },
        _child_subscriptions(payout.child),
    )


def _notify_streak(payout):
    from .push import _child_dashboard_url, _child_subscriptions, _send

    _send(
        {
            "title": _("Perfect week!"),
            "body": _("You earned a %(amount)s AUD streak bonus.") % {"amount": payout.amount},
            "url": _child_dashboard_url("istorija"),
            "tag": f"streak-{payout.pk}",
        },
        _child_subscriptions(payout.child),
    )
