"""The FML StructureMap executor: applies an R4 -> R5 map to a FHIR body (a camelCased dict).

The engine walks the resource group's rules (and its ``extends`` chain), evaluating each rule's
source against the R4 body and building the R5 body via the four transforms these maps use
(``create``, ``copy``, ``translate``; ``evaluate`` is Subscription-only and skipped). Datatype
recursion happens through ``dependent`` group calls, resolved either by name or -- for the
anonymous default alias -- by the runtime type of the argument (see ``cross_version_type_index``).

Source and target variables are tracked in **separate pools** (``svars`` / ``tvars``): the
generated maps reuse one name (e.g. ``vvv``) for both the selected source element and the created
target element, and a ``dependent`` resolves each positional argument against the pool matching
the called group's input ``mode``.

Everything is best-effort: a rule that raises is logged and skipped rather than failing the whole
conversion, and the result is validated as R5 downstream.
"""

import logging
import re

from .cross_version_maps import get_maps
from .cross_version_type_index import R4, R5, child_type, has_field, is_list, model_for

logger = logging.getLogger(__name__)

_MAX_DEPTH = 80
_CONDITION_RE = re.compile(r"^\s*(.+?)\s*(!=|=)\s*(.+?)\s*$")
# Newer maps parenthesise the condition (``(s = 'allergy')``); older ones do not (``type = 'allergy'``).
_WRAPPING_PARENS_RE = re.compile(r"^\((.*)\)$")


class XVerError(Exception):
    """No R4 -> R5 mapping is available for the requested resource type."""


class _Var:
    """A bound FML variable: its dict/scalar ``value`` and the R5 model class of its type
    (``model`` is ``None`` for primitives), plus the type name for choice-key flattening."""

    __slots__ = ("value", "model", "tname")

    def __init__(self, value, model, tname=None):
        self.value = value
        self.model = model
        self.tname = tname


class _Engine:
    def __init__(self, maps=None):
        self.maps = maps or get_maps()

    # -- entry point --

    def transform(self, resource_type, r4_body):
        group = self.maps.group_for(resource_type)
        if group is None:
            raise XVerError(f"No R4->R5 StructureMap for resource type {resource_type!r}")
        # Each side is typed against its own release: the body is R4, the result is R5.
        src = _Var(dict(r4_body), model_for(resource_type, R4), resource_type)
        tgt = _Var({}, model_for(resource_type, R5), resource_type)
        self._run_group(group, [src, tgt], depth=0)
        result = _prune(tgt.value)
        result["resourceType"] = resource_type
        return result

    # -- group / rule execution --

    def _run_group(self, group, args, depth):
        if depth > _MAX_DEPTH:
            logger.warning("cross_version: recursion guard hit at group %s", group.get("name"))
            return
        svars, tvars = {}, {}
        for spec, var in zip(group.get("input", []), args):
            (svars if spec.get("mode") == "source" else tvars)[spec["name"]] = var

        parent = group.get("extends")
        if parent:
            parent_group = self.maps.group_for(parent)
            if parent_group is not None:
                self._run_group(parent_group, args, depth + 1)

        for rule in group.get("rule", []):
            try:
                self._run_rule(rule, svars, tvars, depth)
            except Exception as exc:  # best-effort: drop this field, keep converting.
                logger.debug("cross_version: dropped rule %s in %s: %s", rule.get("name"), group.get("name"), exc)

    def _run_rule(self, rule, svars, tvars, depth):
        sources = rule.get("source", [])
        if not sources:
            return
        source = sources[0]
        context = svars.get(source.get("context"))
        if context is None or not isinstance(context.value, dict):
            return

        element = source.get("element")
        if element is None:
            bindings = [context]  # context-only source: rebinds the same object
        else:
            json_key = _choice_key(context.value, element, source.get("type"))
            raw = context.value.get(json_key)
            if raw is None:
                return
            # The source body is R4, so type it against the R4 models -- never the R5 ones, which
            # would mistype every element that changed between the releases.
            model, tname = child_type(context.model, json_key, R4)
            if model is None and source.get("type"):
                # Primitive element: prefer the rule's declared FHIR type over the introspected
                # name. child_type reports fhir.resources' Python annotation (``bool``/``str``),
                # which loses the FHIR type needed to rebuild a choice key on the target
                # (``boolean``/``dateTime``/``code``) -- e.g. ``deceasedBoolean`` would otherwise
                # become ``deceasedBool``. Also covers a choice element with no matching R4 field.
                tname = source["type"]
                model = model_for(tname, R4)
            items = raw if isinstance(raw, list) else [raw]
            bindings = [_Var(item, model, tname) for item in items]

        for binding in bindings:
            local_s = dict(svars)
            if source.get("variable"):
                local_s[source["variable"]] = binding
            if source.get("condition") and not self._eval_condition(source["condition"], local_s, binding):
                continue
            local_t = dict(tvars)
            for target in rule.get("target", []):
                self._apply_target(target, local_s, local_t)
            dependents = rule.get("dependent", [])
            children = rule.get("rule", [])
            for dependent in dependents:
                self._run_dependent(dependent, local_s, local_t, depth)
            for child in children:
                self._run_rule(child, local_s, local_t, depth)
            if not dependents and not children:
                self._run_implicit_dependent(rule, local_s, local_t, depth)

    # -- transforms --

    def _apply_target(self, target, svars, tvars):
        context = tvars.get(target.get("context"))
        if context is None or not isinstance(context.value, dict):
            return
        transform = target.get("transform")
        element = target.get("element")

        if transform == "copy":
            value = self._param_value(target["parameter"][0], svars, tvars)
            if element is not None and value is not None:
                context.value[element] = value
            return

        if transform == "translate":
            params = target.get("parameter", [])
            code = self._param_value(params[0], svars, tvars)
            url = params[1].get("valueString") if len(params) > 1 else None
            if element is not None and code is not None:
                context.value[element] = self.maps.translate(url, code)
            return

        if transform in (None, "create") and element is not None:
            # An explicit ``create`` or a bare ``tgt.x as v`` target (no transform) both create
            # the element and bind its variable for the following dependent group.
            self._apply_create(target, svars, tvars, context, element)
            return

        # ``evaluate`` (Subscription only) and any other transform are unsupported -> skipped.

    def _apply_create(self, target, svars, tvars, context, element):
        # The variable a ``create`` binds carries the *source* element into the target under the
        # same name (in svars); its type decides the flattened choice key and the child model.
        var_name = target.get("variable")
        source_var = svars.get(var_name) if var_name else None
        json_key, child_model = _target_key(context.model, element, source_var)

        # A primitive-typed create is just a scalar copy (no datatype group to recurse into).
        if source_var is not None and source_var.model is None and not isinstance(source_var.value, (dict, list)):
            if is_list(context.model, json_key):
                context.value.setdefault(json_key, []).append(source_var.value)
            else:
                context.value[json_key] = source_var.value
            if var_name:
                tvars[var_name] = _Var(source_var.value, None, source_var.tname)
            return

        new_obj = {}
        if is_list(context.model, json_key):
            context.value.setdefault(json_key, []).append(new_obj)
        else:
            context.value[json_key] = new_obj
        if var_name:
            tvars[var_name] = _Var(new_obj, child_model, getattr(source_var, "tname", None))

    def _datatype_group(self, src_var, tgt_var):
        """The group that converts ``src_var``'s type to ``tgt_var``'s -- the dispatch behind both
        the explicit ``DefaultMappingGroupAnonymousAlias`` dependent and the implicit one."""
        if src_var is None or src_var.model is None:
            return None  # primitive: value already copied by the create short-circuit.
        src_type = src_var.model.__name__
        tgt_type = tgt_var.model.__name__ if tgt_var is not None and tgt_var.model is not None else None
        group = None
        if tgt_type and tgt_type != src_type:
            # A type-changing element (e.g. R4 Reference -> R5 CodeableReference) dispatches to the
            # HL7 conversion group for that (source, target) pair, which nests correctly
            # (Reference2CodeableReference -> .reference; CodeableConcept2CodeableReference ->
            # .concept). Falls back to the same-type default if there is no conversion group.
            group = self.maps.conversion_group_for(src_type, tgt_type)
        if group is None:
            # Datatypes resolve to their type-default group; backbone elements (typeMode absent)
            # resolve by a group whose name matches the type (ObservationComponent, ...).
            group = self.maps.default_group_for(src_type) or self.maps.group_for(src_type)
        return group

    def _run_implicit_dependent(self, rule, svars, tvars, depth):
        """Recurse into the datatype group a rule *implies*.

        In FML a bare ``src.code -> tgt.code`` between typed structures means "copy, converting the
        element's type" -- the engine is expected to dispatch to that type's conversion group. The
        v0.1.0 pack was published by a tool that materialised this as an explicit
        ``DefaultMappingGroupAnonymousAlias`` dependent on nearly every rule; the current publisher
        emits no dependent at all. Synthesising it here makes the engine work with either pack.
        """
        for target in rule.get("target", []):
            if target.get("transform") not in (None, "create") or target.get("element") is None:
                continue
            var_name = target.get("variable")
            if not var_name:
                continue
            src_var, tgt_var = svars.get(var_name), tvars.get(var_name)
            if tgt_var is None or tgt_var.model is None:
                continue  # nothing was created, or it was a primitive scalar copy
            group = self._datatype_group(src_var, tgt_var)
            if group is not None:
                self._run_group(group, [src_var, tgt_var], depth + 1)

    def _run_dependent(self, dependent, svars, tvars, depth):
        name = dependent.get("name")
        params = dependent.get("parameter", [])
        first = svars.get(params[0].get("valueId")) if params else None

        if name == "DefaultMappingGroupAnonymousAlias":
            second = tvars.get(params[1].get("valueId")) if len(params) > 1 else None
            group = self._datatype_group(first, second)
        else:
            group = self.maps.group_for(name)
        if group is None:
            return

        args = []
        for spec, param in zip(group.get("input", []), params):
            vid = param.get("valueId")
            pool = svars if spec.get("mode") == "source" else tvars
            var = pool.get(vid)
            if var is None:  # tolerate name reuse across pools
                var = (tvars if pool is svars else svars).get(vid)
            args.append(var)
        if len(args) != len(group.get("input", [])) or any(a is None for a in args):
            return
        self._run_group(group, args, depth + 1)

    # -- helpers --

    def _param_value(self, param, svars, tvars):
        if "valueId" in param:
            name = param["valueId"]
            var = svars.get(name) or tvars.get(name)
            return var.value if var is not None else None
        for key in ("valueString", "valueBoolean", "valueInteger", "valueDecimal", "valueUri"):
            if key in param:
                return param[key]
        return None

    def _eval_condition(self, condition, svars, binding):
        # Supports ``<path> = 'literal'`` / ``<path> != 'literal'`` / ``<path> = <var>``, with or
        # without wrapping parens. Anything richer (FHIRPath ``.all()``, ``as`` casts) is
        # unsupported -> the rule is skipped (a documented lossy edge).
        condition = condition.strip()
        wrapped = _WRAPPING_PARENS_RE.match(condition)
        if wrapped and "(" not in wrapped.group(1) and ")" not in wrapped.group(1):
            condition = wrapped.group(1)
        match = _CONDITION_RE.match(condition)
        if not match:
            logger.debug("cross_version: unsupported condition skipped: %s", condition)
            return False
        lhs, op, rhs = match.groups()
        left = self._resolve_path(lhs, svars, binding)
        if rhs.startswith("'") and rhs.endswith("'"):
            right = rhs[1:-1]
        else:
            var = svars.get(rhs)
            right = var.value if var is not None else rhs
        return left == right if op == "=" else left != right

    def _resolve_path(self, path, svars, binding):
        if "." in path:
            head, tail = path.split(".", 1)
            var = svars.get(head)
            value = var.value if var is not None else None
            for part in tail.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            return value
        if path in svars:
            return svars[path].value
        if isinstance(binding.value, dict):
            return binding.value.get(path)
        return None


def _choice_key(container, element, type_hint):
    """Flatten a choice element to its FHIR-JSON key: ``effective`` + ``dateTime`` ->
    ``effectiveDateTime``.

    A rule's ``type`` is a *condition*, so it only implies a flattened key when the element really is
    a choice. Where a rule types a non-choice element the key is unchanged, so prefer whichever key
    the source actually carries.
    """
    if not (element and type_hint):
        return element
    flattened = element + type_hint[:1].upper() + type_hint[1:]
    if flattened not in container and element in container:
        return element
    return flattened


def _target_key(target_model, element, source_var):
    """``(json_key, child_model)`` for creating ``element`` on ``target_model``.

    A direct (non-choice) element keeps its name; a choice element is flattened using the source
    variable's type name (``effective`` + ``Quantity`` -> ``effectiveQuantity``)."""
    if target_model is not None and has_field(target_model, element):
        child_model, _ = child_type(target_model, element)
        return element, child_model
    tname = None
    if source_var is not None:
        tname = source_var.model.__name__ if source_var.model is not None else source_var.tname
    if not tname:
        return element, (source_var.model if source_var else None)
    key = element + tname[:1].upper() + tname[1:]
    child_model, _ = child_type(target_model, key)
    if child_model is None and source_var is not None:
        child_model = source_var.model
    return key, child_model


def _prune(value):
    """Recursively drop ``None`` and empty dict/list leaves (empty ``create`` containers whose
    dependent produced nothing). Booleans, ``0`` and other scalars are preserved."""
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            pruned = _prune(val)
            if pruned is None or (isinstance(pruned, (dict, list)) and not pruned):
                continue
            out[key] = pruned
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            pruned = _prune(item)
            if pruned is None or (isinstance(pruned, (dict, list)) and not pruned):
                continue
            out.append(pruned)
        return out
    return value


def transform_to_r5(resource_type, r4_body):
    """Convert an R4 FHIR resource (camelCased dict) to R5. Raises :class:`XVerError` if no map
    exists for ``resource_type``."""
    return _Engine().transform(resource_type, r4_body)


# ---------------------------------------------------------------------------
# Data-loss detection
# ---------------------------------------------------------------------------


def _collect_scalars(node, out):
    if isinstance(node, dict):
        for value in node.values():
            _collect_scalars(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_scalars(value, out)
    elif node is not None:
        out.add(str(node))


def _subtree_survives(node, surviving):
    if isinstance(node, dict):
        return any(_subtree_survives(v, surviving) for v in node.values())
    if isinstance(node, list):
        return any(_subtree_survives(v, surviving) for v in node)
    return node is None or str(node) in surviving


def dropped_field_paths(r4_body, r5_body):
    """Best-effort list of R4 element paths whose data did not survive the conversion.

    Works by comparing scalar **leaf values**: an R4 leaf whose value appears nowhere in
    ``r5_body`` is reported as dropped, and a fully-dropped subtree is collapsed to its highest
    path (a lost ``activity.detail`` reports ``activity`` once, not each leaf). Because the match
    is on value, a genuine R4 -> R5 *rename* that preserves the value is **not** reported -- only
    real data loss is. This is a heuristic (a dropped value that happens to duplicate a surviving
    one elsewhere is missed); ``resourceType`` is ignored as structural.
    """
    surviving = set()
    _collect_scalars(r5_body, surviving)
    paths = []

    def walk(node, path):
        if not _subtree_survives(node, surviving):
            paths.append(path)
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    for key, value in r4_body.items():
        if key == "resourceType":
            continue
        walk(value, key)
    return paths
