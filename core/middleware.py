import logging

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware

logger = logging.getLogger(__name__)


class NullOriginCsrfMiddleware(CsrfViewMiddleware):
    """Relax CSRF checks in DEBUG mode for local API testing tools.

    Bruno's embedded OAuth browser sends Origin: null and does not
    persist cookies, so both the origin check and the cookie check fail.
    This middleware skips CSRF verification entirely when DEBUG=True and
    the Origin header is 'null' or missing (embedded browser behavior).
    Production (DEBUG=False) is completely unaffected.
    """

    def process_view(self, request, callback, callback_args, callback_kwargs):
        origin = request.META.get("HTTP_ORIGIN")
        if settings.DEBUG and (origin is None or origin == "null"):
            request.csrf_processing_done = True
            return None
        return super().process_view(request, callback, callback_args, callback_kwargs)
