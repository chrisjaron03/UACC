"""
Semantic Knowledge Graph — entity-relation store with cross-session
persistence.

Models applications, windows, UI elements, and their relationships
as a directed graph with typed edges. Enables query-based retrieval
of past knowledge to inform current decisions.

Storage: JSON file at ~/.uacc/semantic_graph.json
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_SEMANTIC_GRAPH_PATH = os.path.expanduser("~/.uacc/semantic_graph.json")


class RelationType(str, Enum):
    """Typed relationships between entities."""

    CONTAINS = "contains"              # Window contains button
    HAS_MENU = "has_menu"              # App has menu bar
    HAS_BUTTON = "has_button"          # App/window has a button
    HAS_INPUT = "has_input"            # App/window has input field
    HAS_TAB = "has_tab"                # App/window has a tab
    HAS_LIST = "has_list"              # App/window has a list
    HAS_TITLE = "has_title"            # App/window has title text
    LAUNCHES = "launches"              # Shortcut launches app
    FOCUSES = "focuses"                # Action focuses window
    OPENS = "opens"                    # Click opens something
    CLOSES = "closes"                  # Action closes something
    TYPES_INTO = "types_into"          # Action types into element
    SELECTS = "selects"                # Action selects option
    NAVIGATES_TO = "navigates_to"      # Navigate to location
    SIMILAR_TO = "similar_to"          # App is similar to another app
    FOLLOWED_BY = "followed_by"        # Pattern: action A followed by action B

    @classmethod
    def from_action(cls, action_name: str) -> Optional[RelationType]:
        mapping = {
            "click": cls.OPENS,
            "type": cls.TYPES_INTO,
            "hotkey": cls.FOCUSES,
            "focus_window": cls.FOCUSES,
            "launch_app": cls.LAUNCHES,
            "select": cls.SELECTS,
            "navigate": cls.NAVIGATES_TO,
        }
        return mapping.get(action_name)


@dataclass
class SemanticEntity:
    """A node in the knowledge graph."""

    id: str
    name: str
    entity_type: str  # "app", "window", "element", "pattern"
    properties: Dict[str, Any] = field(default_factory=dict)
    last_seen: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "last_seen": self.last_seen,
        }


@dataclass
class SemanticRelation:
    """An edge in the knowledge graph."""

    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)
    last_seen: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value,
            "weight": self.weight,
            "properties": self.properties,
            "last_seen": self.last_seen,
        }


class SemanticGraph:
    """Persistent knowledge graph for cross-session learning.

    Usage:
        graph = SemanticGraph()
        graph.ensure_entity("notepad", "Notepad", "app")
        graph.add_relation("notepad", "file_menu", RelationType.HAS_MENU)
        graph.save()
    """

    def __init__(self, path: str = _SEMANTIC_GRAPH_PATH):
        self._path = path
        self._entities: Dict[str, SemanticEntity] = {}
        self._relations: List[SemanticRelation] = []
        self._adjacency: Dict[str, List[SemanticRelation]] = {}
        self._load()

    def ensure_entity(
        self, entity_id: str, name: str, entity_type: str, properties: Optional[Dict[str, Any]] = None
    ) -> SemanticEntity:
        now = datetime.now(timezone.utc).isoformat()
        if entity_id not in self._entities:
            self._entities[entity_id] = SemanticEntity(
                id=entity_id,
                name=name,
                entity_type=entity_type,
                properties=properties or {},
                last_seen=now,
            )
            logger.debug("Created entity: %s (%s)", name, entity_type)
        else:
            self._entities[entity_id].last_seen = now
            if properties:
                self._entities[entity_id].properties.update(properties)
        return self._entities[entity_id]

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        weight: float = 1.0,
        properties: Optional[Dict[str, Any]] = None,
    ) -> SemanticRelation:
        now = datetime.now(timezone.utc).isoformat()
        # Check if relation already exists; update weight if so
        for rel in self._relations:
            if (rel.source_id == source_id and rel.target_id == target_id
                    and rel.relation_type == relation_type):
                rel.weight = max(rel.weight, weight)
                rel.last_seen = now
                if properties:
                    rel.properties.update(properties)
                return rel

        rel = SemanticRelation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            weight=weight,
            properties=properties or {},
            last_seen=now,
        )
        self._relations.append(rel)
        self._adjacency.setdefault(source_id, []).append(rel)
        self._adjacency.setdefault(target_id, []).append(rel)
        return rel

    def record_action_sequence(
        self,
        app_name: str,
        action_name: str,
        element_label: str,
        result: str,
    ) -> None:
        """Record a successful action for cross-session pattern learning.

        Creates/appends to the app entity, creates element entity,
        and links them with the appropriate relation type.
        """
        if not app_name:
            return

        app_id = app_name.lower().replace(" ", "_")
        self.ensure_entity(app_id, app_name, "app")

        if element_label:
            el_id = f"{app_id}__{element_label.lower().replace(' ', '_')}"
            self.ensure_entity(el_id, element_label, "element")
            rel_type = RelationType.from_action(action_name) or RelationType.CONTAINS
            self.add_relation(app_id, el_id, rel_type, weight=1.0)

        if result == "success":
            # Boost weight for successful interactions
            if element_label:
                el_id = f"{app_id}__{element_label.lower().replace(' ', '_')}"
                for rel in self._relations:
                    if rel.source_id == app_id and rel.target_id == el_id:
                        rel.weight = min(rel.weight + 0.1, 2.0)

    def query(self, entity_id: str) -> List[SemanticRelation]:
        """Get all relations for an entity."""
        return [
            r for r in self._relations
            if r.source_id == entity_id or r.target_id == entity_id
        ]

    def query_by_type(
        self, entity_id: str, relation_type: RelationType, outgoing: bool = True
    ) -> List[SemanticRelation]:
        """Get relations of a specific type."""
        return [
            r for r in self._relations
            if r.relation_type == relation_type
            and (r.source_id == entity_id if outgoing else r.target_id == entity_id)
        ]

    def find_entity(self, name: str, entity_type: Optional[str] = None) -> Optional[SemanticEntity]:
        """Find an entity by name (case-insensitive partial match)."""
        name_lower = name.lower()
        for entity in self._entities.values():
            if entity_type and entity.entity_type != entity_type:
                continue
            if name_lower in entity.name.lower():
                return entity
        return None

    def find_similar_apps(self, app_name: str) -> List[str]:
        """Find apps similar to the given app name."""
        app_entity = self.find_entity(app_name, entity_type="app")
        if not app_entity:
            return []

        similar: List[str] = []
        for rel in self.query(app_entity.id):
            if rel.relation_type == RelationType.SIMILAR_TO:
                target = self._entities.get(rel.target_id)
                if target:
                    similar.append(target.name)
        return similar

    def get_app_patterns(self, app_name: str) -> Dict[str, Any]:
        """Get known UI patterns for an application."""
        app_entity = self.find_entity(app_name, entity_type="app")
        if not app_entity:
            return {}

        patterns: Dict[str, List[str]] = {}
        for rel in self.query(app_entity.id):
            target = self._entities.get(rel.target_id)
            if target and target.entity_type == "element":
                action_type = rel.relation_type.value
                patterns.setdefault(action_type, []).append(target.name)

        return {
            "name": app_entity.name,
            "entity_id": app_entity.id,
            "patterns": patterns,
            "last_seen": app_entity.last_seen,
        }

    def merge(self, other: SemanticGraph) -> int:
        """Merge another graph into this one. Returns number of new items."""
        count = 0
        for entity in other._entities.values():
            if entity.id not in self._entities:
                self._entities[entity.id] = entity
                count += 1

        existing_pairs = {
            (r.source_id, r.target_id, r.relation_type.value)
            for r in self._relations
        }
        for rel in other._relations:
            pair = (rel.source_id, rel.target_id, rel.relation_type.value)
            if pair not in existing_pairs:
                self._relations.append(rel)
                self._adjacency.setdefault(rel.source_id, []).append(rel)
                self._adjacency.setdefault(rel.target_id, []).append(rel)
                count += 1

        if count:
            self.save()
        return count

    def summary(self) -> Dict[str, Any]:
        return {
            "entities": len(self._entities),
            "relations": len(self._relations),
            "apps": sum(1 for e in self._entities.values() if e.entity_type == "app"),
            "elements": sum(1 for e in self._entities.values() if e.entity_type == "element"),
        }

    def save(self) -> None:
        """Persist the graph to disk."""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            data = {
                "entities": [e.to_dict() for e in self._entities.values()],
                "relations": [r.to_dict() for r in self._relations],
            }
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to save semantic graph: %s", exc)

    def _load(self) -> None:
        """Load the graph from disk."""
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path) as f:
                data = json.load(f)
            for ed in data.get("entities", []):
                entity = SemanticEntity(**ed)
                self._entities[entity.id] = entity
            for rd in data.get("relations", []):
                rel = SemanticRelation(
                    source_id=rd["source_id"],
                    target_id=rd["target_id"],
                    relation_type=RelationType(rd["relation_type"]),
                    weight=rd.get("weight", 1.0),
                    properties=rd.get("properties", {}),
                    last_seen=rd.get("last_seen", ""),
                )
                self._relations.append(rel)
                self._adjacency.setdefault(rel.source_id, []).append(rel)
                self._adjacency.setdefault(rel.target_id, []).append(rel)
            logger.info(
                "Loaded semantic graph: %d entities, %d relations",
                len(self._entities),
                len(self._relations),
            )
        except Exception as exc:
            logger.warning("Failed to load semantic graph from %s: %s", self._path, exc)
