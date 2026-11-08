"""Explicit live-provider controls. Select this file; never collected by default."""
import pytest

from tests.controller_test_support import controller_lifetime, translation_service
from tests.controller_test_support import TranslationRequestHandler
from tests.test_controller import (
    test_controller_lookup_endpoint,
    test_controller_session_retext_long_single_mode_text,
    test_controller_api_token_auth_headers,
)


@pytest.fixture
def running_controller(tmp_path):
    # The same three public-action test functions, with the real Google engine.
    with translation_service(TranslationRequestHandler) as translator:
        with controller_lifetime(tmp_path, translator) as world:
            yield world
