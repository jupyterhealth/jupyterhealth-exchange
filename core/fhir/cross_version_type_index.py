"""Element -> type resolution for the R4 -> R5 engine, sourced from the installed
``fhir.resources`` models.

The cross-version StructureMaps dispatch datatype ``dependent`` calls by the *runtime type* of the
element being mapped (e.g. ``Observation.code`` -> the ``CodeableConcept`` group). The maps do not
carry that type -- and are not required to: a map declares
``uses "http://hl7.org/fhir/4.0/StructureDefinition/Observation" as source``, so a conforming engine
is expected to read the element's type from the **R4** definition. (A rule's ``source.type`` is *not*
a type declaration; per the spec it "works as a condition on the mapping - use for polymorphic
elements", i.e. it discriminates a choice element such as ``value[x]``.)

We therefore resolve **each side against its own release**:

- **source** elements against the **R4** models (``fhir.resources.R4B``), and
- **target** elements against the **R5** models (``fhir.resources``).

Typing a source element from R5 is wrong whenever the element changed between the releases, and it
fails *silently*: an element that kept its name but changed type (R4 ``Device.definition`` is a
``Reference``; R5's is a ``CodeableReference``) resolves "successfully" to the R5 type, and the value
is then converted as something it is not and lost. That is the whole reason this module is
release-aware.

> **R4B stands in for R4.** ``fhir.resources`` ships no R4 (4.0.1) models, only R4B (4.3.0). The two
> agree on every element the R4 -> R5 maps read across the configured aux resources (verified against
> the official R4 StructureDefinitions). They diverge for the resources R4B actually revised --
> ``Evidence``, ``EvidenceVariable``, ``Citation``, ``Subscription{Status,Topic}`` and the
> ``MedicinalProduct*`` family -- so **re-check this assumption before adding one of those** to
> ``aux_resources``.

Per element:

- a field annotation whose class name ends in ``Type`` (e.g. ``CodeableConceptType``) is a
  **complex** type; its model class is the annotation name minus the ``Type`` suffix
  (``CodeableConcept``), looked up in that release's registry;
- any other annotation (``Code``, ``String``, ``DateTime`` ...) is a **primitive** -- the engine
  copies its scalar value directly instead of recursing into a datatype group.

Choice elements (``value[x]``) are already flattened in FHIR JSON (``valueQuantity``), so the
flattened key is looked up directly.
"""

import functools
import importlib
import pkgutil
import typing

import fhir.resources as _r5
import fhir.resources.R4B as _r4
from fhir.resources.core.fhirabstractmodel import FHIRAbstractModel

R4 = "R4"
R5 = "R5"

_PACKAGES = {R4: _r4, R5: _r5}


@functools.cache
def _registry(release):
    """All model classes for ``release`` keyed by class name (resources + datatypes + backbone
    elements). Built once per release by walking the package."""
    package = _PACKAGES[release]
    reg = {}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in ("core", "fhirtypes", "fhirtypesvalidators"):
            continue
        try:
            module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        except Exception:
            continue
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, FHIRAbstractModel):
                reg.setdefault(obj.__name__, obj)
    return reg


def model_for(type_name, release=R5):
    """Model class for a FHIR type/resource name in ``release``, or ``None`` (a primitive)."""
    if not type_name:
        return None
    return _registry(release).get(type_name)


def _innermost(annotation):
    """Peel ``Optional`` / ``List`` / ``Union`` down to the first concrete inner class."""
    args = typing.get_args(annotation)
    if not args:
        return annotation
    for arg in args:
        if arg is type(None):
            continue
        return _innermost(arg)
    return annotation


@functools.cache
def _fields_by_json_key(parent_cls):
    """Fields of ``parent_cls`` keyed by their **FHIR JSON** name.

    ``fhir.resources`` renames fields that collide with Python keywords (``Encounter.class`` becomes
    ``class_fhir``) and carries the wire name in the pydantic alias, so index by alias.
    """
    fields = {}
    for name, field in getattr(parent_cls, "__fields__", {}).items():
        fields[getattr(field, "alias", None) or name] = field
    return fields


def _field(parent_cls, json_key):
    if parent_cls is None:
        return None
    return _fields_by_json_key(parent_cls).get(json_key)


def has_field(parent_cls, json_key):
    """Does ``parent_cls`` declare ``json_key`` directly (i.e. it is not a flattened choice)?"""
    return _field(parent_cls, json_key) is not None


def child_type(parent_cls, json_key, release=R5):
    """``(model_cls_or_None, type_name)`` for ``parent_cls.<json_key>``.

    ``release`` names the registry the *child* model is resolved in -- pass the release
    ``parent_cls`` itself came from.

    ``model_cls`` is ``None`` for a primitive-typed child (copy the scalar) or an unknown field.
    ``type_name`` is the complex model name (``CodeableConcept``) or the primitive annotation name
    (``DateTime``); ``None`` when the field is unknown.
    """
    field = _field(parent_cls, json_key)
    if field is None:
        return None, None
    inner = _innermost(field.annotation)
    name = getattr(inner, "__name__", None)
    if name and name.endswith("Type"):
        base = name[:-4]
        return model_for(base, release), base
    return None, name


def _contains_list(annotation):
    if typing.get_origin(annotation) is list:
        return True
    return any(_contains_list(arg) for arg in typing.get_args(annotation))


def is_list(parent_cls, json_key):
    """Is ``parent_cls.<json_key>`` a repeating (list) element?"""
    field = _field(parent_cls, json_key)
    if field is None:
        return False
    return _contains_list(field.annotation)
