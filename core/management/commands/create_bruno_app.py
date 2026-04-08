from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from oauth2_provider.models import AccessToken, get_application_model
from oauthlib.common import generate_token

from core.models import JheUser, Organization, PractitionerOrganization


class Command(BaseCommand):
    help = "Create an OAuth2 application and access token for Bruno API testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="sam@example.com",
            help="Email of the user to generate a token for (default: sam@example.com)",
        )

    def handle(self, *args, **options):
        Application = get_application_model()
        name = "Bruno API Collection"

        app, created = Application.objects.get_or_create(
            name=name,
            defaults={
                "redirect_uris": settings.SITE_URL + "/auth/callback/simple/",
                "client_type": "public",
                "authorization_grant_type": "authorization-code",
                "skip_authorization": True,
                "algorithm": "RS256",
                "created": timezone.now(),
                "updated": timezone.now(),
                "hash_client_secret": False,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created OAuth app: {name}"))
        else:
            self.stdout.write(f"Using existing OAuth app: {name}")

        # Create or refresh access token
        email = options["email"]
        try:
            user = JheUser.objects.get(email=email)
        except JheUser.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"User not found: {email}"))
            return

        # Delete any expired tokens for this app/user
        AccessToken.objects.filter(application=app, user=user).delete()

        token = AccessToken.objects.create(
            user=user,
            application=app,
            token=generate_token(),
            expires=timezone.now() + timezone.timedelta(days=365),
            scope="openid",
        )

        # Ensure the user has access to all organizations
        self._ensure_org_access(user)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Access token created successfully!"))
        self.stdout.write("")
        self.stdout.write(f"  Token: {token.token}")
        self.stdout.write(f"  User:  {email}")
        self.stdout.write(f"  Expires: {token.expires}")
        self.stdout.write("")
        self.stdout.write("Set this as ACCESS_TOKEN in your Bruno environment.")

    def _ensure_org_access(self, user):
        """Add the user as a manager to all non-ROOT organizations they aren't already in."""
        practitioner = getattr(user, "practitioner", None)
        if practitioner is None:
            self.stdout.write(self.style.WARNING(f"  {user.email} has no Practitioner profile - skipping org access"))
            return

        existing_org_ids = set(
            PractitionerOrganization.objects.filter(practitioner=practitioner).values_list("organization_id", flat=True)
        )

        orgs_to_add = Organization.objects.exclude(type="root").exclude(id__in=existing_org_ids)
        if not orgs_to_add.exists():
            self.stdout.write(f"  {user.email} already has access to all organizations")
            return

        links = [
            PractitionerOrganization(practitioner=practitioner, organization=org, role="manager") for org in orgs_to_add
        ]
        PractitionerOrganization.objects.bulk_create(links)
        self.stdout.write(self.style.SUCCESS(f"  Added {user.email} as manager to {len(links)} organizations"))
