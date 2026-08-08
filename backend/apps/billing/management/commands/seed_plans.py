from django.core.management.base import BaseCommand

from apps.billing.models import BillingPlan


PLANS = [
    {
        'name': '1 AI Employee',
        'slug': 'starter',
        'price_monthly': 19,
        'conversation_limit': 500,
        'token_limit': 200_000,
        'employee_limit': 1,
    },
    {
        'name': '3 AI Employees',
        'slug': 'pro',
        'price_monthly': 49,
        'conversation_limit': 2_500,
        'token_limit': 1_000_000,
        'employee_limit': 3,
    },
    {
        'name': 'Unlimited AI Employees',
        'slug': 'business',
        'price_monthly': 99,
        'conversation_limit': 10_000,
        'token_limit': 5_000_000,
        'employee_limit': 999,
    },
]


class Command(BaseCommand):
    help = 'Seed 1 / 3 / Unlimited AI Employee billing plans'

    def handle(self, *args, **options):
        for data in PLANS:
            obj, created = BillingPlan.objects.update_or_create(
                slug=data['slug'],
                defaults=data,
            )
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'{action} plan: {obj.name}')
