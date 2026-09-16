#!/usr/bin/env python3
"""Busca propiedades en SAP SuccessFactors OData V2 usando únicamente $metadata."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import warnings
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

import requests


@dataclass
class PropertyInfo:
    name: str
    type: str = ""
    nullable: Optional[str] = None
    label: str = ""
    picklist: str = ""


@dataclass
class NavigationInfo:
    name: str
    relationship: str = ""
    from_role: str = ""
    to_role: str = ""
    target_entity_type: Optional[str] = None
    target_entity_set: Optional[str] = None


@dataclass
class EntityInfo:
    name: str
    namespace: str = ""
    entity_set: Optional[str] = None
    label: str = ""
    properties: List[PropertyInfo] = field(default_factory=list)
    navigations: List[NavigationInfo] = field(default_factory=list)


@dataclass
class AssociationInfo:
    name: str
    namespace: str
    ends: Dict[str, str] = field(default_factory=dict)


@dataclass
class MetadataModel:
    entities: Dict[str, EntityInfo]
    entity_sets: Dict[str, str]
    associations: Dict[str, AssociationInfo]
    raw_size: int = 0


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def attr(element: ET.Element, name: str, default: str = "") -> str:
    return element.attrib.get(name, default)


def namespaced_attr(element: ET.Element, name: str, default: str = "") -> str:
    """Obtiene un atributo por nombre local, por ejemplo sap:label o sap:picklist."""
    for key, value in element.attrib.items():
        if local_name(key).lower() == name.lower():
            return value
    return default


def short_name(value: str) -> str:
    return value.rsplit(".", 1)[-1]


def odata_root(base_url: str) -> str:
    """Acepta tanto el host como la raíz completa /odata/v2 o /odata/v2ss."""
    value = base_url.rstrip("/")
    if re.search(r"/odata/v2(?:ss)?$", value, re.IGNORECASE):
        return value
    return value + "/odata/v2"


def find_entity(model: MetadataModel, name: str) -> Optional[EntityInfo]:
    if name in model.entities:
        return model.entities[name]
    matches = [e for key, e in model.entities.items() if short_name(key).lower() == name.lower()]
    return matches[0] if len(matches) == 1 else None


def download_metadata(base_url: str, username: str, password: str, insecure: bool = False,
                      timeout: int = 60, cache_path: Optional[str] = None,
                      refresh_cache: bool = False) -> bytes:
    if cache_path and os.path.exists(cache_path) and not refresh_cache:
        with open(cache_path, "rb") as handle:
            return handle.read()
    url = odata_root(base_url) + "/$metadata"
    if insecure:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    try:
        response = requests.get(url, auth=(username, password), timeout=timeout,
                                verify=not insecure, headers={"Accept": "application/xml"})
    except requests.Timeout as exc:
        raise RuntimeError(f"Timeout consultando metadata: {url}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Error de red consultando metadata: {exc}") from exc
    messages = {401: "Credenciales no autorizadas (HTTP 401)",
                403: "Acceso prohibido (HTTP 403)",
                404: "Endpoint de metadata no encontrado (HTTP 404)"}
    if response.status_code in messages:
        raise RuntimeError(messages[response.status_code])
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Error HTTP {response.status_code} consultando metadata") from exc
    if not response.content:
        raise RuntimeError("SuccessFactors devolvió metadata vacía")
    if cache_path:
        parent = os.path.dirname(os.path.abspath(cache_path))
        os.makedirs(parent, exist_ok=True)
        with open(cache_path, "wb") as handle:
            handle.write(response.content)
    return response.content


def parse_metadata(xml_bytes: bytes) -> MetadataModel:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"XML de metadata inválido: {exc}") from exc

    entities: Dict[str, EntityInfo] = {}
    entity_sets: Dict[str, str] = {}
    associations: Dict[str, AssociationInfo] = {}
    schemas = [e for e in root.iter() if local_name(e.tag) == "Schema"]

    for schema in schemas:
        namespace = attr(schema, "Namespace")
        for child in list(schema):
            if local_name(child.tag) == "Association":
                key = f"{namespace}.{attr(child, 'Name')}"
                ends = {}
                for end in child:
                    if local_name(end.tag) == "End":
                        ends[attr(end, "Role")] = attr(end, "Type")
                associations[key] = AssociationInfo(attr(child, "Name"), namespace, ends)

        for child in list(schema):
            if local_name(child.tag) != "EntityType":
                continue
            name = attr(child, "Name")
            key = f"{namespace}.{name}" if namespace else name
            entity = EntityInfo(name=name, namespace=namespace,
                                label=namespaced_attr(child, "label"))
            for item in list(child):
                kind = local_name(item.tag)
                if kind == "Property":
                    entity.properties.append(PropertyInfo(
                        name=attr(item, "Name"), type=attr(item, "Type"),
                        nullable=item.attrib.get("Nullable"),
                        label=namespaced_attr(item, "label"),
                        picklist=namespaced_attr(item, "picklist")))
                elif kind == "NavigationProperty":
                    entity.navigations.append(NavigationInfo(
                        name=attr(item, "Name"), relationship=attr(item, "Relationship"),
                        from_role=attr(item, "FromRole"), to_role=attr(item, "ToRole")))
            entities[key] = entity

        for container in [e for e in list(schema) if local_name(e.tag) == "EntityContainer"]:
            for item in list(container):
                if local_name(item.tag) == "EntitySet":
                    entity_sets[attr(item, "Name")] = attr(item, "EntityType")

    for entity_type, entity_set in entity_sets.items():
        entity = find_entity(MetadataModel(entities, entity_sets, associations), entity_set)
        if entity and not entity.entity_set:
            entity.entity_set = entity_type

    resolve_associations(MetadataModel(entities, entity_sets, associations))
    return MetadataModel(entities, entity_sets, associations, len(xml_bytes))


def resolve_associations(model: MetadataModel) -> MetadataModel:
    for entity in model.entities.values():
        for nav in entity.navigations:
            assoc = model.associations.get(nav.relationship)
            if not assoc:
                assoc = next((a for key, a in model.associations.items()
                              if short_name(key).lower() == short_name(nav.relationship).lower()), None)
            if not assoc:
                continue
            target_type = assoc.ends.get(nav.to_role)
            if not target_type:
                other_roles = [r for r in assoc.ends if r != nav.from_role]
                target_type = assoc.ends.get(other_roles[0]) if other_roles else None
            if target_type:
                nav.target_entity_type = target_type
                target = find_entity(model, target_type)
                if target:
                    nav.target_entity_set = target.entity_set
    return model


def matches(value: str, query: str, exact: bool = False) -> bool:
    if "*" in query or "?" in query:
        return fnmatch.fnmatchcase(value.lower(), query.lower())
    return value.lower() == query.lower() if exact else query.lower() in value.lower()


def odata_navigation_path(path: List[str]) -> str:
    """Convierte una ruta interna entidad/nav/entidad en una ruta OData de navegaciones."""
    return "/".join(path[1:-1:2])


def result_odata_route(item: dict) -> str:
    """Devuelve una ruta legible y válida para OData, sin repetir EntityTypes intermedios."""
    path = item.get("navigation_path") or [item.get("entity", "")]
    root = path[0] if path else item.get("entity", "")
    navigation = item.get("odata_path")
    if navigation is None:
        navigation = odata_navigation_path(path)
    parts = [root] if root else []
    if navigation:
        parts.extend(navigation.split("/"))
    if (item.get("search_type") not in {"RELATED ENTITY", "NAVIGATION"}
            or item.get("match_kind") != "navigation_property"):
        property_name = item.get("property_name", "")
        if property_name and (not parts or parts[-1] != property_name):
            parts.append(property_name)
    return "/".join(parts)


def property_matches(entity: EntityInfo, field_name: str, exact: bool = False) -> Iterable[PropertyInfo]:
    return (p for p in entity.properties if matches(p.name, field_name, exact=exact))


def result(field_name: str, entity: EntityInfo, prop: PropertyInfo, path: List[str], depth: int,
           search_type: str) -> dict:
    is_template = "jobreqtemplate" in (entity.name + " " + (entity.entity_set or "")).lower()
    return {"field": field_name, "entity": entity.name, "entity_set": entity.entity_set,
            "property_name": prop.name, "property_type": prop.type,
            "navigation_path": path or [entity.name], "depth": depth,
            "odata_path": odata_navigation_path(path or [entity.name]),
            "search_type": search_type, "queryable": "NO" if is_template else "UNKNOWN"}


def preferred_target_property(target: Optional[EntityInfo]) -> Optional[PropertyInfo]:
    """Selecciona una propiedad representativa del destino de una navegación."""
    if not target:
        return None
    priorities = ("externalcode", "code", "id", "name")
    for priority in priorities:
        property_info = next((prop for prop in target.properties if prop.name.lower() == priority), None)
        if property_info:
            return property_info
    return target.properties[0] if target.properties else None


def navigation_result(field_name: str, entity: EntityInfo, nav: NavigationInfo,
                      path: List[str], depth: int,
                      target_property: Optional[PropertyInfo] = None) -> dict:
    target_name = nav.target_entity_type or "UNKNOWN"
    target = target_name.rsplit(".", 1)[-1]
    item = {"field": field_name, "entity": entity.name, "entity_set": entity.entity_set,
            "property_name": nav.name, "property_type": target_name,
            "navigation_path": path + [nav.name, target], "depth": depth,
            "odata_path": odata_navigation_path(path + [nav.name, target]),
            "search_type": "NAVIGATION", "match_kind": "navigation_property",
            "queryable": "UNKNOWN"}
    if target_property:
        item["target_property"] = target_property.name
        item["target_property_type"] = target_property.type
    return item


def search_direct_properties(model: MetadataModel, entity_name: str, fields: List[str],
                             exact: bool = False) -> List[dict]:
    entity = find_entity(model, entity_name)
    if not entity:
        raise ValueError(f"Entidad no encontrada: {entity_name}")
    return [result(field_name, entity, prop, [entity.name], 0, "DIRECT")
            for field_name in fields for prop in property_matches(entity, field_name, exact)]


def recursive_navigation_search(model: MetadataModel, entity_name: str, fields: List[str],
                                max_depth: int, exact: bool = False) -> List[dict]:
    root = find_entity(model, entity_name)
    if not root:
        raise ValueError(f"Entidad no encontrada: {entity_name}")
    found: List[dict] = []
    queue = deque([(root, [root.name], 0)])
    visited_entities = {root.name.lower()}
    while queue:
        entity, path, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nav in entity.navigations:
            target = find_entity(model, nav.target_entity_type or "")
            if not target:
                continue
            new_path = path + [nav.name, target.name]
            for field_name in fields:
                if matches(nav.name, field_name, exact=exact):
                    found.append(navigation_result(
                        field_name, entity, nav, path, depth + 1,
                        preferred_target_property(target)))
            if target.name.lower() in visited_entities:
                continue
            visited_entities.add(target.name.lower())
            for field_name in fields:
                for prop in property_matches(target, field_name, exact):
                    found.append(result(field_name, target, prop, new_path, depth + 1, "NAVIGATION"))
            queue.append((target, new_path, depth + 1))
    return found


def search_odata_path_pattern(model: MetadataModel, root_name: str, pattern: str,
                              max_depth: int, max_paths_per_state: int = 3,
                              include_global: bool = False,
                              cancel_check: Optional[Callable[[], bool]] = None,
                              max_results: Optional[int] = None) -> List[dict]:
    """Busca propiedades por una ruta OData relativa, admitiendo ``*`` por directorios.

    Un segmento ``*`` equivale a cero o más directorios de navegación. Dentro
    de un segmento, ``*`` y ``?`` funcionan como comodines de nombre, por
    ejemplo ``*Nav`` o ``custom_*``. Las rutas que no empiezan por la entidad
    raíz se interpretan como parciales, por lo que ``rmk_region/externalCode``
    busca ese sufijo en cualquier rama de la raíz.
    La exploración conserva hasta tres caminos cortos por estado del patrón,
    por lo que muestra rutas alternativas útiles sin recorrer todas las
    combinaciones posibles de directorios.
    """
    root = find_entity(model, root_name)
    if not root:
        raise ValueError(f"Entidad no encontrada: {root_name}")
    if max_paths_per_state < 1:
        raise ValueError("max_paths_per_state debe ser mayor que cero.")
    if max_results is not None and max_results < 1:
        raise ValueError("max_results debe ser mayor que cero.")

    segments = [segment.strip() for segment in pattern.strip().strip("/").split("/") if segment.strip()]
    if not segments:
        raise ValueError("La ruta o patrón no puede estar vacío.")

    root_aliases = {root.name.lower()}
    if root.entity_set:
        root_aliases.add(root.entity_set.lower())
    is_rooted = segments[0].lower() in root_aliases
    if is_rooted:
        segments = segments[1:]
    if not segments:
        raise ValueError("La ruta debe incluir al menos una propiedad o NavigationProperty después de la raíz.")
    is_single_segment = len(segments) == 1
    if not is_rooted and segments[0] != "*":
        segments.insert(0, "*")

    normalized = tuple(segment.lower() for segment in segments)

    def closure(states: Set[int]) -> frozenset[int]:
        """Aplica las transiciones vacías de los comodines ``*``."""
        pending = list(states)
        expanded = set(states)
        while pending:
            index = pending.pop()
            if index < len(normalized) and normalized[index] == "*" and index + 1 not in expanded:
                expanded.add(index + 1)
                pending.append(index + 1)
        return frozenset(expanded)

    def advance(states: frozenset[int], value: str) -> frozenset[int]:
        """Avanza el autómata del patrón con una navegación o propiedad."""
        advanced: Set[int] = set()
        for index in closure(set(states)):
            if index >= len(normalized):
                continue
            token = normalized[index]
            if token == "*":
                advanced.add(index)
            elif (token == value.lower()
                  or (("*" in token or "?" in token)
                      and fnmatch.fnmatchcase(value.lower(), token))):
                advanced.add(index + 1)
        return closure(advanced)

    initial_state = closure({0})
    queue = deque([(root, [root.name], 0, initial_state)])
    state_paths = {(root.name.lower(), initial_state): {()}}
    found: List[dict] = []
    result_paths: Set[Tuple[str, str]] = set()
    navigation_paths: Set[str] = set()

    while queue:
        if cancel_check and cancel_check():
            return []
        entity, path, depth, states = queue.popleft()
        for prop in entity.properties:
            after_property = advance(states, prop.name)
            if len(normalized) not in after_property:
                continue
            odata_path = odata_navigation_path(path)
            result_key = (odata_path.lower(), prop.name.lower())
            if result_key in result_paths:
                continue
            result_paths.add(result_key)
            item = result(pattern, entity, prop, path, depth, "PATH")
            item["path_pattern"] = pattern
            item["match_kind"] = "path_pattern"
            found.append(item)

        if depth >= max_depth:
            continue
        for nav in entity.navigations:
            target = find_entity(model, nav.target_entity_type or "")
            if not target:
                continue
            # Una ruta que vuelve a una entidad ya visitada solo añade ciclos
            # y no ayuda a descubrir directorios nuevos dentro de esta búsqueda.
            if target.name.lower() in {name.lower() for name in path[::2]}:
                continue
            next_states = advance(states, nav.name)
            if not next_states:
                continue
            # Si un comodín coincide con nombres distintos (por ejemplo,
            # ``*Nav`` con degreeNav y con candidateNav), conservarlos por
            # separado evita que una coincidencia oculte a la otra al llegar
            # a la misma entidad de destino.
            state_key = (target.name.lower(), next_states, nav.name.lower())
            new_path = path + [nav.name, target.name]
            nav_odata_path = odata_navigation_path(new_path)
            if len(normalized) in next_states and nav_odata_path.lower() not in navigation_paths:
                navigation_paths.add(nav_odata_path.lower())
                item = navigation_result(pattern, entity, nav, path, depth + 1,
                                         preferred_target_property(target))
                item["search_type"] = "PATH"
                item["path_pattern"] = pattern
                found.append(item)
            odata_path = tuple(odata_navigation_path(new_path).lower().split("/"))
            known_paths = state_paths.setdefault(state_key, set())
            if odata_path in known_paths or len(known_paths) >= max_paths_per_state:
                continue
            known_paths.add(odata_path)
            queue.append((target, new_path, depth + 1, next_states))
    if cancel_check and cancel_check():
        return []
    if include_global and is_single_segment:
        found.extend(global_property_search(model, [pattern], exact=True))
    found.sort(key=lambda item: (
        item.get("search_type") == "GLOBAL",
        item.get("depth", 0),
        result_odata_route(item).lower(),
    ))
    return found[:max_results] if max_results is not None else found


def global_property_search(model: MetadataModel, fields: List[str], exact: bool = False) -> List[dict]:
    return [result(field_name, entity, prop, [entity.name], 0, "GLOBAL")
            for field_name in fields for entity in model.entities.values()
            for prop in property_matches(entity, field_name, exact)]


METADATA_FILTER_ALIASES = {
    "type": "type", "tipo": "type",
    "property": "property", "propiedad": "property",
    "destination": "destination", "destino": "destination",
    "destination_property": "destination_property",
    "destino_propiedad": "destination_property",
    "propiedad_destino": "destination_property",
}


def parse_metadata_filter(expression: str) -> List[List[Tuple[str, str]]]:
    """Analiza filtros sencillos de metadata unidos por AND y OR.

    Cada lista interior agrupa condiciones AND; las listas exteriores se unen
    con OR. Por ejemplo, ``type:Edm.Byte OR destino:PicklistOption AND
    destino_propiedad:externalCode``.
    """
    if not expression.strip():
        return []
    or_groups = re.split(r"\s+OR\s+", expression.strip(), flags=re.IGNORECASE)
    parsed: List[List[Tuple[str, str]]] = []
    for group in or_groups:
        conditions: List[Tuple[str, str]] = []
        for condition in re.split(r"\s+AND\s+", group, flags=re.IGNORECASE):
            key, separator, value = condition.partition(":")
            canonical_key = METADATA_FILTER_ALIASES.get(key.strip().lower())
            if not separator or not canonical_key or not value.strip():
                valid = ", ".join(sorted(set(METADATA_FILTER_ALIASES.values())))
                raise ValueError(
                    f"Filtro de metadata no válido: '{condition.strip()}'. "
                    f"Usa {valid} seguido de ':valor'."
                )
            conditions.append((canonical_key, value.strip()))
        parsed.append(conditions)
    return parsed


def result_destination_entity(model: MetadataModel, item: dict) -> Optional[EntityInfo]:
    """Resuelve el destino cuando un resultado representa una navegación."""
    source = find_entity(model, item.get("entity", ""))
    if source:
        navigation = next(
            (nav for nav in source.navigations
             if nav.name.lower() == item.get("property_name", "").lower()),
            None,
        )
        if navigation and navigation.target_entity_type:
            target = find_entity(model, navigation.target_entity_type)
            if target:
                return target
    return find_entity(model, item.get("property_type", ""))


def filter_metadata_results(model: MetadataModel, results: List[dict], expression: str) -> List[dict]:
    """Filtra resultados por tipo, propiedad, entidad destino o sus propiedades."""
    groups = parse_metadata_filter(expression)
    if not groups:
        return results

    def condition_matches(item: dict, key: str, value: str) -> bool:
        if key == "type":
            return matches(item.get("property_type", ""), value)
        if key == "property":
            return matches(item.get("property_name", ""), value)
        target = result_destination_entity(model, item)
        if not target:
            return False
        if key == "destination":
            return matches(target.name, value) or matches(target.entity_set or "", value)
        if key == "destination_property":
            return any(matches(prop.name, value) for prop in target.properties)
        return False

    return [item for item in results if any(
        all(condition_matches(item, key, value) for key, value in group)
        for group in groups
    )]


def search_properties_by_attribute(model: MetadataModel, root_name: str, query: str,
                                   attribute: str, max_depth: int,
                                   exact: bool = False,
                                   include_global: bool = True) -> List[dict]:
    """Busca por tipo, sap:picklist o etiqueta y conserva la ruta BFS más corta."""
    root = find_entity(model, root_name)
    if not root:
        raise ValueError(f"Entidad no encontrada: {root_name}")
    attr_name = attribute.lower()

    def property_value(prop: PropertyInfo) -> str:
        return {"type": prop.type, "picklist": prop.picklist, "label": prop.label}.get(attr_name, "")

    def selected(entity: EntityInfo) -> Iterable[PropertyInfo]:
        return (prop for prop in entity.properties if matches(property_value(prop), query, exact))

    found: List[dict] = []
    seen_result_keys: Set[Tuple[str, str, str]] = set()

    def add_matches(entity: EntityInfo, path: List[str], depth: int, search_type: str) -> None:
        for prop in selected(entity):
            key = (entity.name.lower(), prop.name.lower(), search_type)
            if key not in seen_result_keys:
                seen_result_keys.add(key)
                item = result(query, entity, prop, path, depth, search_type)
                item["matched_attribute"] = attribute
                item["matched_value"] = property_value(prop)
                found.append(item)

    add_matches(root, [root.name], 0, "DIRECT")
    queue = deque([(root, [root.name], 0)])
    visited = {root.name.lower()}
    while queue:
        entity, path, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nav in entity.navigations:
            target = find_entity(model, nav.target_entity_type or "")
            if not target or target.name.lower() in visited:
                continue
            visited.add(target.name.lower())
            new_path = path + [nav.name, target.name]
            add_matches(target, new_path, depth + 1, "NAVIGATION")
            queue.append((target, new_path, depth + 1))
    if include_global:
        for entity in model.entities.values():
            for prop in selected(entity):
                key = (entity.name.lower(), prop.name.lower(), "GLOBAL")
                if key not in seen_result_keys:
                    seen_result_keys.add(key)
                    item = result(query, entity, prop, [entity.name], 0, "GLOBAL")
                    item["matched_attribute"] = attribute
                    item["matched_value"] = property_value(prop)
                    found.append(item)
    return found


def search_entities(model: MetadataModel, text: str) -> List[dict]:
    return [{"entity": e.name, "entity_set": e.entity_set,
             "properties": [asdict(p) for p in e.properties]}
            for e in model.entities.values()
            if matches(e.name, text) or matches(e.entity_set or "", text)]


def search_related_entities(model: MetadataModel, root_name: str, text: str,
                            max_depth: int) -> List[dict]:
    root = find_entity(model, root_name)
    if not root:
        raise ValueError(f"Entidad no encontrada: {root_name}")
    found: List[dict] = []

    def walk(entity: EntityInfo, path: List[str], depth: int, seen: Set[str]):
        if depth >= max_depth:
            return
        for nav in entity.navigations:
            target = find_entity(model, nav.target_entity_type or "")
            if not target or target.name.lower() in seen:
                continue
            new_path = path + [nav.name, target.name]
            if matches(target.name, text) or matches(target.entity_set or "", text):
                found.append({"entity": target.name, "entity_set": target.entity_set,
                              "navigation_property": nav.name, "depth": depth + 1,
                              "navigation_path": new_path,
                              "odata_path": odata_navigation_path(new_path),
                              "queryable": "NO" if "jobreqtemplate" in target.name.lower() else "UNKNOWN"})
            walk(target, new_path, depth + 1, seen | {target.name.lower()})

    walk(root, [root.name], 0, {root.name.lower()})
    return found


def dump_entity(model: MetadataModel, name: str) -> dict:
    entity = find_entity(model, name)
    if not entity:
        raise ValueError(f"Entidad no encontrada: {name}")
    return {"entity": entity.name, "entity_set": entity.entity_set,
            "properties": [asdict(p) for p in entity.properties],
            "navigation_properties": [asdict(n) for n in entity.navigations]}


def list_navigation_tree(model: MetadataModel, name: str, max_depth: int) -> str:
    root = find_entity(model, name)
    if not root:
        raise ValueError(f"Entidad no encontrada: {name}")
    lines = [root.name]

    def walk(entity: EntityInfo, prefix: str, depth: int, seen: Set[str]):
        if depth >= max_depth:
            return
        for index, nav in enumerate(entity.navigations):
            target = find_entity(model, nav.target_entity_type or "")
            target_name = target.name if target else (nav.target_entity_type or "UNKNOWN")
            branch = "└── " if index == len(entity.navigations) - 1 else "├── "
            lines.append(prefix + branch + nav.name + " -> " + target_name)
            if target and target.name.lower() not in seen:
                child_prefix = prefix + ("    " if index == len(entity.navigations) - 1 else "│   ")
                walk(target, child_prefix, depth + 1, seen | {target.name.lower()})
    walk(root, "", 0, {root.name.lower()})
    return "\n".join(lines)


def build_select_expand(model: MetadataModel, root_name: str, results: List[dict],
                        odata_filter: Optional[str] = None,
                        base_url: Optional[str] = None) -> dict:
    root = find_entity(model, root_name)
    root_key = next((p.name for p in root.properties if p.name.lower() in {"jobreqid", "externalcode", "id"}), None) if root else None
    selects = {root_key} if root_key else set()
    expands: Set[str] = set()
    for item in results:
        if item["search_type"] == "DIRECT":
            selects.add(item["property_name"])
            continue
        if item["search_type"] not in {"NAVIGATION", "PATH"}:
            continue
        nav_path = item.get("odata_path")
        nav_names = nav_path.split("/") if nav_path else item["navigation_path"][1:-1:2]
        if item.get("match_kind") != "navigation_property":
            property_path = "/".join(nav_names + [item["property_name"]])
            selects.add(property_path)
        elif item.get("target_property"):
            selects.add("/".join(nav_names + [item["target_property"]]))
        for i in range(1, len(nav_names) + 1):
            expands.add("/".join(nav_names[:i]))
    if odata_filter:
        # Detecta rutas como status/externalCode o nav1/nav2/property en el filtro.
        for route in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)+\b", odata_filter):
            segments = route.split("/")
            for i in range(1, len(segments)):
                expands.add("/".join(segments[:i]))
    select_text = ",".join(sorted(x for x in selects if x))
    expand_text = ",".join(sorted(expands, key=lambda x: (x.count("/"), x)))
    entity_set = root.entity_set if root else root_name
    query = (odata_root(base_url) if base_url else "/odata/v2").rstrip("/") + f"/{entity_set}"
    params = []
    if select_text:
        params.append("$select=" + select_text)
    if expand_text:
        params.append("$expand=" + expand_text)
    if odata_filter:
        params.append("$filter=" + quote(odata_filter, safe="/$=,'()"))
    return {"select": select_text, "expand": expand_text, "filter": odata_filter,
            "relative_url": query + ("?" + "&".join(params) if params else "")}


def print_results(results: List[dict], query: Optional[dict] = None, color: bool = True) -> None:
    try:
        from rich import print as rich_print
        printer = rich_print if color else print
    except ImportError:
        printer = print
    for item in results:
        printer(f"FIELD: {item['field']}\nENTITY: {item['entity']}\nENTITY SET: {item['entity_set']}\n"
                f"PROPERTY NAME: {item['property_name']}\nPROPERTY TYPE: {item['property_type']}\n"
                f"NAVIGATION PATH: {' -> '.join(item['navigation_path'])}\nDEPTH: {item['depth']}\n"
                f"SEARCH TYPE: {item['search_type']}\nQUERYABLE: {item['queryable']}\n")
    if query:
        printer("=== RECOMMENDED ODATA QUERY ===")
        printer(query["relative_url"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="JobRequisition")
    parser.add_argument("--fields", nargs="*")
    parser.add_argument("--path-pattern",
                        help="ruta OData o patrón; * equivale a cero o más segmentos, por ejemplo */jobRequisition/*/degreeNav/externalCode")
    parser.add_argument("--filter", dest="odata_filter",
                        help="expresión OData $filter, por ejemplo: jobReqId eq '2381' and status/externalCode eq 'Open'")
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--exact", action="store_true", help="coincidencia exacta del nombre de propiedad")
    parser.add_argument("--direct-only", action="store_true", help="buscar solo en la entidad raíz")
    parser.add_argument("--list-nav", action="store_true")
    parser.add_argument("--search-entity")
    parser.add_argument("--search-related-entity")
    parser.add_argument("--dump-entity")
    parser.add_argument("--output")
    parser.add_argument("--metadata-cache", help="archivo local para reutilizar $metadata")
    parser.add_argument("--refresh-metadata", action="store_true", help="forzar descarga del metadata")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_depth < 0:
        print("--max-depth debe ser >= 0", file=sys.stderr)
        return 2
    base_url = os.environ.get("SF_BASE_URL")
    username = os.environ.get("SF_USERNAME")
    password = os.environ.get("SF_PASSWORD")
    if not all((base_url, username, password)):
        print("Define SF_BASE_URL, SF_USERNAME y SF_PASSWORD como variables de entorno.", file=sys.stderr)
        return 2
    try:
        xml_bytes = download_metadata(base_url, username, password, args.insecure,
                                      cache_path=args.metadata_cache,
                                      refresh_cache=args.refresh_metadata)
        model = parse_metadata(xml_bytes)
        payload = {"metadata_bytes": model.raw_size}
        if args.search_entity:
            payload["entities"] = search_entities(model, args.search_entity)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif args.search_related_entity:
            payload["related_entities"] = search_related_entities(
                model, args.entity, args.search_related_entity, args.max_depth)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif args.dump_entity:
            payload["entity"] = dump_entity(model, args.dump_entity)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif args.list_nav:
            print(list_navigation_tree(model, args.entity, args.max_depth))
            payload["navigation_tree"] = list_navigation_tree(model, args.entity, args.max_depth)
        elif args.path_pattern:
            results = search_odata_path_pattern(model, args.entity, args.path_pattern, args.max_depth)
            query = build_select_expand(model, args.entity, results, args.odata_filter, base_url)
            payload.update({"entity": args.entity, "path_pattern": args.path_pattern,
                            "max_depth": args.max_depth, "results": results,
                            "recommended_query": query})
            print_results(results, query, not args.no_color)
        elif args.fields:
            direct = search_direct_properties(model, args.entity, args.fields, args.exact)
            navigation = [] if args.direct_only else recursive_navigation_search(
                model, args.entity, args.fields, args.max_depth, args.exact)
            global_results = [] if args.direct_only else global_property_search(model, args.fields, args.exact)
            results = direct + navigation + global_results
            query = build_select_expand(model, args.entity, direct + navigation, args.odata_filter, base_url)
            payload.update({"entity": args.entity, "fields": args.fields, "max_depth": args.max_depth,
                            "results": results, "recommended_query": query})
            print_results(results, query, not args.no_color)
        else:
            print("Usa --fields, --path-pattern, --list-nav, --search-entity o --dump-entity.", file=sys.stderr)
            return 2
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
