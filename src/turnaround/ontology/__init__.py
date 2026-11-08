"""경량 온톨로지: rdflib 개념 계층과 pySHACL 제약 (최소판, 4단계 게이트웨이 재검사용)."""

from .graph import TA, build_graph, load_concepts
from .validate import load_shapes, validate

__all__ = ["TA", "build_graph", "load_concepts", "load_shapes", "validate"]
