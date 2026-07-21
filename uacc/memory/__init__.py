"""
UACC Cross-Session Memory — semantic knowledge graph that persists
across agent sessions. Enables the agent to remember applications,
UI patterns, and successful action sequences.

Components:
  - SemanticGraph: entity-relation knowledge graph with persistence
  - PatternDiscovery: auto-extract reusable patterns from episodes
"""

from uacc.memory.semantic_graph import RelationType, SemanticGraph, SemanticEntity, SemanticRelation

__all__ = ["SemanticGraph", "SemanticEntity", "SemanticRelation", "RelationType"]
