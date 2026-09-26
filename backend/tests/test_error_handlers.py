"""Details of the error handlers that the endpoint tests do not reach."""

import pytest
from starlette.requests import Request

from app.routes import errors


@pytest.mark.parametrize(
    ("location", "field"),
    [
        (("body", "text"), "text"),
        (("query", "page_size"), "page_size"),
        (("path", "complaint_id"), "complaint_id"),
        (("body", "address", "street"), "address.street"),  # a nested field keeps its path
        (("body", "items", 2, "name"), "items.name"),  # list indexes are not field names
        (("body",), "body"),  # the whole body is wrong or missing
        (("body", 4), "body"),  # malformed JSON reports a character offset
        ((), "request"),
    ],
)
def test_the_field_name_is_what_the_client_sent(
    location: tuple[int | str, ...], field: str
) -> None:
    assert errors._field_name(location) == field


@pytest.mark.parametrize(
    "handler",
    [errors._invalid_transition, errors._complaint_not_found, errors._validation_failed],
)
def test_a_handler_hands_back_any_exception_that_is_not_its_own(handler: object) -> None:
    unrelated = RuntimeError("not ours")
    request = Request({"type": "http"})

    with pytest.raises(RuntimeError, match="not ours"):
        handler(request, unrelated)  # type: ignore[operator]
