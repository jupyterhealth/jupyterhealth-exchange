"""US Core search parameters, ``_sort`` and ``_summary`` for the FHIR resource endpoint.

Every search resolves to exactly ONE backing store (see core/views/fhir.py), so the filters
here are applied to whichever authorized queryset that store produced -- never across a union:

  * **mapped** resources (Observation, Patient, ...) filter/sort with the Django ORM over the
    backing model's columns. The per-param field mapping is declared as each mapped resource's
    ``__search`` block in fhir_config.json.
  * **auxiliary** resources (everything stored opaquely in FhirAuxResource) filter/sort with a
    small Postgres JSONB query builder: each ``__search`` param names a FHIRPath into the stored
    ``fhir_data`` body, which is compiled to a raw ``jsonb_path_exists`` / ``#>>`` predicate and
    attached to the authorized queryset via ``RawSQL`` (so the ORM keeps enforcing authorization,
    while the JSONB matching runs as raw SQL). Values only ever reach Postgres as bound
    parameters -- jsonpath ``$vars`` for the path-exists predicates, positional params for ``#>>``
    date comparisons -- so no user input is ever interpolated into SQL or a jsonpath expression.

The supported params per resource are the US Core "supported searches" set (CapabilityStatement
Summary of Resource/Profile Capabilities). The resource-agnostic ``_id`` / ``_lastUpdated`` params
and the ``patient`` / ``patient.organization`` / ``patient._has:Group:member`` location filters are
handled upstream (see core/views/fhir.py); this module handles the resource-specific params plus
``_sort`` and ``_summary``.
"""

import json
import re

from django.db.models import BooleanField, F, Q, TextField
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import ValidationError as DRFValidationError

from core.fhir.config import (
    aux_search_params,
    aux_sort_date,
    mapped_search_params,
    mapped_sort_date,
)

# US Core date-comparator prefixes -> the operator/lookup they express.
_DATE_COMPARATORS = {"ge": "gte", "le": "lte", "gt": "gt", "lt": "lt"}
_SQL_OPERATORS = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_search_params(queryset, resource_type, request, store):
    """Apply the resource-specific US Core filters and ``_sort`` for ``store`` (mapped|aux)."""
    if store == "mapped":
        queryset = _apply_mapped_filters(queryset, resource_type, request)
    elif store == "aux":
        queryset = _apply_aux_filters(queryset, resource_type, request)
    return _apply_sort(queryset, resource_type, request, store)


def summary_count_requested(request):
    """True when ``_summary=count`` -- the caller returns only the searchset total, no entries."""
    return (request.GET.get("_summary") or "").strip().lower() == "count"


# ---------------------------------------------------------------------------
# Request-param extraction
# ---------------------------------------------------------------------------


def _norm(key):
    # Separator-insensitive param-key normalization. The camel-case query-param parser may rewrite
    # a key's separators (Group->_group, and hyphen/underscore spellings vary), so we compare
    # 'clinical-status', 'clinical_status', 'clinicalStatus' etc. as the same param.
    return key.lower().replace("_", "").replace("-", "")


def _match_params(request, names):
    """Map each declared param name to the list of raw values sent for it (repeat = AND)."""
    wanted = {_norm(name): name for name in names}
    found = {}
    for key in request.GET.keys():
        canonical = wanted.get(_norm(key))
        if canonical is not None:
            found.setdefault(canonical, []).extend(request.GET.getlist(key))
    return found


def _paths(spec):
    path = spec.get("path")
    return path if isinstance(path, list) else [path]


def _split_token(value):
    """A FHIR token ``system|code`` -> ``(system, code)``; a bare ``code`` -> ``(None, code)``."""
    if "|" in value:
        system, _, code = value.partition("|")
        return system, (code or None)
    return None, value


def _regex_escape(value):
    # Escape regex metacharacters so a string/reference value is matched literally by like_regex.
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", value)


def _jsonpath_literal(value):
    # A jsonpath double-quoted string literal. Postgres' like_regex requires a literal pattern (not a
    # $var), so a user value that becomes a regex is embedded here -- backslash and double-quote are
    # escaped so it cannot break out of the literal. The whole jsonpath is still a bound parameter,
    # so this is jsonpath quoting only, never SQL interpolation.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Auxiliary store: Postgres JSONB query builder
# ---------------------------------------------------------------------------
#
# Path-membership predicates (token/identifier/code/string/reference) compile to
#   jsonb_path_exists(fhir_data, '<jsonpath>', '<vars-json>')
# The jsonpath is built only from the config path + fixed $var names (never user input); every
# user value is passed in the vars JSON. jsonpath runs in lax mode, so a ``.member`` step over an
# array auto-unwraps -- one predicate matches a CodeableConcept's ``coding`` array, a repeating
# element, or a scalar alike. Date predicates instead extract text with ``#>>`` and compare
# lexically (ISO-8601 order == chronological order), COALESCE-ing polymorphic ``[x]`` paths.


def _path_exists(jsonpath, variables):
    return "jsonb_path_exists(fhir_data, %s, %s)", [jsonpath, json.dumps(variables)]


def _coding_term(paths, field, value):
    # Match a Coding/CodeableConcept element whose <field> (code, or value for Identifier) -- and
    # system, when the token carries one -- equals the given token.
    system, code = _split_token(value)
    sql_parts, params = [], []
    for path in paths:
        conditions, variables = [], {}
        if code is not None:
            conditions.append(f"@.{field} == $v")
            variables["v"] = code
        if system:
            conditions.append("@.system == $s")
            variables["s"] = system
        predicate = " && ".join(conditions) or "true"
        sql, prm = _path_exists(f"$.{path} ? ({predicate})", variables)
        sql_parts.append(sql)
        params += prm
    return "(" + " OR ".join(sql_parts) + ")", params


def _code_term(paths, value):
    # Match a scalar code element (a plain FHIR ``code``); the token's system, if any, is ignored.
    _, code = _split_token(value)
    sql_parts, params = [], []
    for path in paths:
        sql, prm = _path_exists(f"$.{path} ? (@ == $v)", {"v": code})
        sql_parts.append(sql)
        params += prm
    return "(" + " OR ".join(sql_parts) + ")", params


def _string_term(paths, value):
    # FHIR string search: case-insensitive starts-with over any of the paths.
    pattern = _jsonpath_literal("^" + _regex_escape(value.strip()))
    sql_parts, params = [], []
    for path in paths:
        sql, prm = _path_exists(f'$.{path} ? (@ like_regex {pattern} flag "i")', {})
        sql_parts.append(sql)
        params += prm
    return "(" + " OR ".join(sql_parts) + ")", params


def _reference_term(paths, value):
    # Match a reference by full value ("Patient/123") or by a bare id ("123", i.e. any ".../123").
    value = value.strip()
    suffix = _jsonpath_literal("/" + _regex_escape(value) + "$")
    sql_parts, params = [], []
    for path in paths:
        sql, prm = _path_exists(f"$.{path} ? (@ == $full || @ like_regex {suffix})", {"full": value})
        sql_parts.append(sql)
        params += prm
    return "(" + " OR ".join(sql_parts) + ")", params


_TERM_BUILDERS = {
    "token": lambda paths, value: _coding_term(paths, "code", value),
    "identifier": lambda paths, value: _coding_term(paths, "value", value),
    "code": _code_term,
    "string": _string_term,
    "reference": _reference_term,
}


def _pg_path(path):
    # A dotted FHIRPath -> the text[] Postgres expects for the #>> operator ("a.b" -> ["a", "b"]).
    return path.split(".")


def _aux_date_term(paths, raw_values):
    # ISO-8601 lexical comparison over COALESCE(polymorphic [x] paths). Repeated params AND.
    coalesce = "COALESCE(" + ", ".join(["fhir_data #>> %s"] * len(paths)) + ")"
    coalesce_params = [_pg_path(path) for path in paths]
    and_sql, params = [], []
    for raw in raw_values:
        comparator = _DATE_COMPARATORS.get(raw[:2])
        value = raw[2:] if comparator else raw
        if comparator:
            and_sql.append(f"({coalesce}) {_SQL_OPERATORS[comparator]} %s")
            params += coalesce_params + [value]
        else:
            # A bare date/dateTime: prefix match, so 2020-01-01 matches that whole instant/day.
            and_sql.append(f"({coalesce}) LIKE %s")
            params += coalesce_params + [value + "%"]
    return " AND ".join(and_sql), params


def _aux_param_sql(spec, raw_values):
    ptype = spec["type"]
    paths = _paths(spec)
    if ptype == "date":
        return _aux_date_term(paths, raw_values)
    builder = _TERM_BUILDERS.get(ptype)
    if builder is None:
        return None, []
    and_sql, params = [], []
    for raw in raw_values:  # repeated params AND together
        or_sql = []
        for piece in raw.split(","):  # comma-separated values OR together
            piece = piece.strip()
            if not piece:
                continue
            sql, prm = builder(paths, piece)
            or_sql.append(sql)
            params += prm
        if or_sql:
            and_sql.append("(" + " OR ".join(or_sql) + ")")
    if not and_sql:
        return None, []
    return " AND ".join(and_sql), params


def _apply_aux_filters(queryset, resource_type, request):
    specs = aux_search_params(resource_type)
    for index, (param, raw_values) in enumerate(_match_params(request, specs.keys()).items()):
        sql, params = _aux_param_sql(specs[param], raw_values)
        if not sql:
            continue
        flag = f"_search{index}"
        queryset = queryset.annotate(**{flag: RawSQL(sql, params, output_field=BooleanField())}).filter(**{flag: True})
    return queryset


# ---------------------------------------------------------------------------
# Mapped store: Django ORM filters
# ---------------------------------------------------------------------------


def _mapped_field_type(model, field):
    try:
        return model._meta.get_field(field).get_internal_type()
    except Exception:
        return None


def _apply_mapped_date(queryset, fields, raw_values, flag):
    expr = F(fields[0]) if len(fields) == 1 else Coalesce(*[F(field) for field in fields])
    queryset = queryset.annotate(**{flag: expr})
    is_date_field = _mapped_field_type(queryset.model, fields[0]) == "DateField"
    for raw in raw_values:
        comparator = _DATE_COMPARATORS.get(raw[:2])
        value = raw[2:] if comparator else raw
        # A DateField column, or a plain YYYY-MM-DD value, is compared at day precision; a value
        # carrying a time is compared as an instant against the (DateTime) column.
        if is_date_field or len(value) <= 10:
            day = parse_date(value[:10])
            if day is None:
                raise DRFValidationError(f"Invalid date value: '{raw}'.")
            base = flag if is_date_field else f"{flag}__date"
            lookup = f"{base}__{comparator}" if comparator else base
            queryset = queryset.filter(**{lookup: day})
            continue
        dt = parse_datetime(value)
        if dt is None:
            raise DRFValidationError(f"Invalid date value: '{raw}'.")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        lookup = f"{flag}__{comparator}" if comparator else flag
        queryset = queryset.filter(**{lookup: dt})
    return queryset


def _apply_mapped_filters(queryset, resource_type, request):
    specs = mapped_search_params(resource_type)
    for index, (param, raw_values) in enumerate(_match_params(request, specs.keys()).items()):
        spec = specs[param]
        ptype = spec["type"]
        if ptype == "const":
            # The mapped resource renders this element as a fixed value: the whole result matches
            # when a requested token's code equals it, and nothing matches otherwise.
            wanted = {_split_token(piece)[1] for raw in raw_values for piece in raw.split(",")}
            if spec["value"] not in wanted:
                return queryset.none()
            continue
        if ptype == "date":
            queryset = _apply_mapped_date(queryset, _paths(spec), raw_values, f"_search{index}")
            continue
        fields = _paths(spec)
        condition = Q()
        for raw in raw_values:  # repeated params AND
            alternatives = Q()
            for piece in raw.split(","):  # comma values OR
                piece = piece.strip()
                if not piece:
                    continue
                if ptype == "token":
                    _, code = _split_token(piece)
                    for field in fields:
                        alternatives |= Q(**{field: code})
                elif ptype == "string":
                    for field in fields:
                        alternatives |= Q(**{f"{field}__istartswith": piece})
            condition &= alternatives
        queryset = queryset.filter(condition)
    return queryset


# ---------------------------------------------------------------------------
# _sort (both stores)
# ---------------------------------------------------------------------------


def _annotate_sort_date(queryset, resource_type, store):
    """Annotate the resolved ``date`` sort key onto ``queryset``; return (order_name, queryset)."""
    if store == "mapped":
        fields = mapped_sort_date(resource_type)
        if not fields:
            return None, queryset
        if len(fields) == 1:
            return fields[0], queryset  # order by the column directly
        return "_sortdate", queryset.annotate(_sortdate=Coalesce(*[F(field) for field in fields]))
    paths = aux_sort_date(resource_type)
    if not paths:
        return None, queryset
    sql = "COALESCE(" + ", ".join(["fhir_data #>> %s"] * len(paths)) + ")"
    params = [_pg_path(path) for path in paths]
    return "_sortdate", queryset.annotate(_sortdate=RawSQL(sql, params, output_field=TextField()))


def _apply_sort(queryset, resource_type, request, store):
    raw = request.GET.get("_sort")
    if not raw:
        return queryset
    order = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        descending = token.startswith("-")
        name = _norm(token[1:] if descending else token)
        if name == "lastupdated":
            order.append("-last_updated" if descending else "last_updated")
        elif name == "date":
            sort_name, queryset = _annotate_sort_date(queryset, resource_type, store)
            if sort_name is None:
                continue
            # The date sort key is nullable (a row may have no effective time).
            # Postgres puts NULLs first on DESC by default, which would surface
            # undated rows as the "newest" — keep them last in either direction.
            key = F(sort_name)
            order.append(key.desc(nulls_last=True) if descending else key.asc(nulls_last=True))
        # Unknown sort keys are ignored (FHIR permits a server to ignore unsupported _sort keys).
    if order:
        # A pk tiebreaker makes the ordering total: LIMIT/OFFSET paging over a
        # non-unique sort key is otherwise nondeterministic across queries, so
        # rows sharing a sort value could be skipped or duplicated across pages.
        order.append("pk")
        queryset = queryset.order_by(*order)
    return queryset
