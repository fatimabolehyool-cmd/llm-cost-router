"""End-to-end tests run against the running Docker Compose stack with MOCK models (`make e2e`).

They are skipped unless E2E_ROUTER_URL is set, so `pytest` alone (and CI) never needs Docker.
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="E2E_ROUTER_URL not set (run `make e2e`)")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.e2e)
            if not os.environ.get("E2E_ROUTER_URL"):
                item.add_marker(skip)
