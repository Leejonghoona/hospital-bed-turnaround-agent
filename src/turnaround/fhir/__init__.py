"""FHIR R4 경계 어댑터 (EVT-7). 내부 이벤트가 정본이다.

대응표: DECISIONS.md "2026-11-08 FHIR R4 입력 어댑터", "2026-11-12 FHIR 어댑터 표준 의미로 정리".
"""

from .adapter import bundle_to_events, events_to_bundle, layout_to_locations, locations_to_layout

__all__ = ["bundle_to_events", "events_to_bundle", "layout_to_locations", "locations_to_layout"]
