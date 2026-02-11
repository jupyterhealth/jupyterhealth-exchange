from django.db.models.query import RawQuerySet

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .admin_pagination import PaginatedRawQuerySet


class FHIRBundlePagination(PageNumberPagination):
    """
    FHIR Bundle pagination using database-level pagination with raw SQL.
    No in-memory result sets or mock objects.
    """

    # FHIR standard query parameters
    page_size_query_param = "_count"
    page_query_param = "_page"
    page_size = 20
    max_page_size = 1000  # TBD: May need to be adjusted based on database performance and testing

    def paginate_queryset(self, queryset, request, view=None):
        if isinstance(queryset, RawQuerySet):
            queryset = PaginatedRawQuerySet.from_raw(queryset)
        return super().paginate_queryset(queryset, request, view=view)

    def get_paginated_response(self, data):
        """Return FHIR-compliant Bundle response with pagination"""
        if self.page.paginator.count == 0:
            total_pages = 1
        else:
            total_pages = self.page.paginator.count // self.page.paginator.per_page
            if self.page.paginator.count % self.page.paginator.per_page:
                total_pages += 1

        response_data = {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": self.page.paginator.count,
            "entry": data,
            "link": self._get_fhir_links(),
            "meta": {
                "pagination": {
                    "page": self.page.number,
                    "pageSize": self.page.paginator.per_page,
                    "totalPages": total_pages,
                }
            },
        }

        return Response(response_data)

    def _get_fhir_links(self):
        """Generate FHIR Bundle links for pagination"""
        links = []
        prev_link = self.get_previous_link()
        next_link = self.get_next_link()
        if prev_link:
            links.append({"relation": "previous", "url": prev_link})
        if next_link:
            links.append({"relation": "next", "url": next_link})
        return links
