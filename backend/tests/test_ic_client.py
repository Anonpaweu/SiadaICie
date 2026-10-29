"""Unit tests for PKPIntercityScraper (ic_client.py) and caching."""
import pytest
from backend.app.scraper.ic_client import (
    PKPIntercityScraper,
    GraphicReservationUnavailableError,
)
from backend.app.db.database import get_cached_item, set_cached_item


def test_parse_seatmap_json():
    """Verify that _parse_seatmap_json accurately extracts free and occupied seats."""
    sample_payload = {
        "carriages": [
            {
                "number": 8,
                "seats": [
                    {"number": "13", "status": "FREE"},
                    {"number": "14", "status": "OCCUPIED"},
                ],
            },
            {
                "number": 6,
                "seats": [
                    {"number": "40", "status": "AVAILABLE"},
                    {"number": "42", "status": "RESERVED"},
                ],
            },
        ]
    }
    parsed = PKPIntercityScraper._parse_seatmap_json(sample_payload)
    assert parsed["8_13"] == 1
    assert parsed["8_14"] == 0
    assert parsed["6_40"] == 1
    assert parsed["6_42"] == 0


def test_sqlite_caching_ttl():
    """Verify that set_cached_item and get_cached_item respect TTL."""
    key = "test_key_abc_123"
    payload = {"status": "ok", "count": 42}

    # Set cache item
    set_cached_item(key, payload)

    # Immediate retrieval should succeed
    cached = get_cached_item(key, ttl_seconds=600)
    assert cached == payload

    # Retrieval with 0s TTL should treat it as expired
    expired = get_cached_item(key, ttl_seconds=0)
    assert expired is None

