from rest_framework.pagination import PageNumberPagination

from django.db import connection
from django.db.models import sql
from django.db.models.query import RawQuerySet


# https://stackoverflow.com/questions/32191853/best-way-to-paginate-a-raw-sql-query-in-a-django-rest-listapi-view#:~:text=A%20more%20efficient%20solution%20than,in%20your%20raw%20SQL%20query
class PaginatedRawQuerySet(RawQuerySet):
    def __init__(self, raw_query, **kwargs):
        super(PaginatedRawQuerySet, self).__init__(raw_query, **kwargs)
        self.original_raw_query = raw_query
        self._count = None

    @classmethod
    def from_raw(cls, qs: RawQuerySet):
        """Create a PaginatedRawQuerySet from a RawQuerySet"""
        # same as _clone, but translates class
        return cls(
            raw_query=qs.raw_query,
            model=qs.model,
            params=qs.params,
            translations=qs.translations,
            using=qs.db,
            hints=qs._hints,
        )

    def __getitem__(self, k):
        """
        Retrieves an item or slice from the set of results.
        """
        if not isinstance(
            k,
            (
                slice,
                int,
            ),
        ):
            raise TypeError(f"Can only index by int or slice, not {k:r}")
        if (not isinstance(k, slice) and (k >= 0)) or (
            isinstance(k, slice) and (k.start is None or k.start >= 0) and (k.stop is None or k.stop >= 0)
        ):
            raise ValueError("Negative indexing is not supported.")

        if "offset" in self.params or "limit" in self.params:
            # TODO: this is actually quite doable,
            # but I noticed the code below does it wrong,
            # so better to error than be wrong
            raise ValueError("Cannot slice an already sliced query")

        if isinstance(k, slice):
            qs = self._clone()
            if k.start is not None:
                start = int(k.start)
            else:
                start = None
            if k.stop is not None:
                stop = int(k.stop)
            else:
                stop = None
            qs.set_limits(start, stop)
            return qs

        qs = self._clone()
        qs.set_limits(k, k + 1)
        return list(qs)[0]

    def count(self):
        """Compute the count

        Still executes the full query,
        but at least does not fetch results.
        """
        if self._count is not None:
            return self._count

        # run count without fetch
        count_query = f"SELECT COUNT(*) FROM ({self.raw_query}) AS _tocount"
        with connection.cursor() as cursor:
            cursor.execute(count_query, self.params)
            self._count = cursor.fetchone()[0]
        return self._count

    def set_limits(self, start, stop):
        limit_offset = ""

        if not self.params:
            self.params = {}
        if start is None:
            start = 0
        elif start > 0:
            self.params["offset"] = start
            limit_offset = " OFFSET %(offset)s"
        if stop is not None:
            self.params["limit"] = stop - start
            limit_offset = "LIMIT %(limit)s" + limit_offset

        self.raw_query = self.original_raw_query + limit_offset
        self.query = sql.RawQuery(sql=self.raw_query, using=self.db, params=self.params)

    def __len__(self):
        return self.count()


class CustomPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    page_query_param = "page"
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        if isinstance(queryset, RawQuerySet):
            queryset = PaginatedRawQuerySet.from_raw(queryset)
        return super().paginate_queryset(queryset, request, view)
