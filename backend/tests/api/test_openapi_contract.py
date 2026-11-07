"""OpenAPI is the single frozen contract for the read-only research API."""

from __future__ import annotations

import json
from pathlib import Path

from backend.tests.api.test_research_routes import (
    EXPECTED_GET_PATHS,
    EXPECTED_POST_PATHS,
    EXPECTED_RESEARCH_GET_PATHS,
    make_research_app,
)


def test_openapi_contains_only_declared_operations_and_no_trading_surface(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()

    session_path = "/api/v1/instruments/{symbol}/options/pulse/sessions/{session_id}"
    assert set(schema["paths"]) == EXPECTED_GET_PATHS | EXPECTED_POST_PATHS | {session_path}
    for path, operations in schema["paths"].items():
        expected_methods = {"put", "delete"} if path == session_path else set()
        if path in EXPECTED_GET_PATHS:
            expected_methods.add("get")
        if path in EXPECTED_POST_PATHS:
            expected_methods.add("post")
        assert set(operations) == expected_methods, path
        if "get" in operations:
            assert "requestBody" not in operations["get"]
    serialized_paths = json.dumps(schema["paths"]).lower()
    assert not any(
        forbidden in serialized_paths
        for forbidden in ("/trade", "/order", "/position", "/account", "/unlock")
    )
    schema_names = {name.lower() for name in schema["components"]["schemas"]}
    assert not any(
        token in name
        for name in schema_names
        for token in ("tradeorder", "accountunlock", "placeorder")
    )


def _parameters(schema: dict[str, object], path: str) -> dict[str, dict[str, object]]:
    paths = schema["paths"]
    assert isinstance(paths, dict)
    operation = paths[path]["get"]  # type: ignore[index]
    return {item["name"]: item for item in operation["parameters"]}  # type: ignore[index]


def _resolved_schema(
    document: dict[str, object], value: dict[str, object]
) -> dict[str, object]:
    variants = value.get("anyOf")
    if isinstance(variants, list):
        value = next(
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("type") != "null"
        )
    reference = value.get("$ref")
    if not isinstance(reference, str):
        return value
    name = reference.rsplit("/", maxsplit=1)[-1]
    return document["components"]["schemas"][name]  # type: ignore[index,return-value]


def test_openapi_documents_strict_query_allowlists(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()
    screen = _parameters(schema, "/api/v1/screen")
    history = _parameters(schema, "/api/v1/instruments/{symbol}/history")
    forecast = _parameters(schema, "/api/v1/instruments/{symbol}/forecast")

    assert screen["horizon"]["schema"]["enum"] == [1, 5, 20]
    assert _resolved_schema(schema, screen["prediction_basis"]["schema"])["enum"] == [
        "forecast",
        "executable",
    ]
    assert _resolved_schema(schema, screen["return_basis"]["schema"])["enum"] == [
        "capital_gain",
        "executable",
    ]
    assert screen["page"]["schema"]["minimum"] == 1
    assert screen["page"]["schema"]["maximum"] == 100
    assert screen["limit"]["schema"]["minimum"] == 1
    assert screen["limit"]["schema"]["maximum"] == 100
    min_probability = _resolved_schema(schema, screen["min_probability"]["schema"])
    max_q10 = _resolved_schema(schema, screen["max_q10"]["schema"])
    assert min_probability["minimum"] == 0
    assert min_probability["maximum"] == 1
    assert max_q10["minimum"] == -1
    assert max_q10["maximum"] == 1
    assert _resolved_schema(schema, screen["confidence"]["schema"])["enum"] == [
        "low",
        "medium",
        "high",
    ]
    assert screen["direction"]["schema"]["$ref"].endswith("/SortDirection")
    assert history["timeframe"]["schema"]["enum"] == ["1d", "60m"]
    assert history["adjustment"]["schema"]["enum"] == [
        "raw",
        "split_adjusted",
        "total_return",
    ]
    assert set(_resolved_schema(schema, forecast["include"]["schema"])["enum"]) == {
        "evidence",
        "similar_cases",
        "evidence,similar_cases",
        "similar_cases,evidence",
    }


def test_forecast_openapi_is_a_discriminated_result(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()
    forecast_operation = schema["paths"]["/api/v1/instruments/{symbol}/forecast"]["get"]
    response_schema = forecast_operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    referenced_name = response_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    envelope = schema["components"]["schemas"][referenced_name]
    data_schema = envelope["properties"]["data"]

    assert data_schema["discriminator"]["propertyName"] == "availability"
    assert len(data_schema["oneOf"]) == 2


def test_openapi_uses_stable_error_envelopes_for_validation(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()
    for path in EXPECTED_RESEARCH_GET_PATHS - {"/api/v1/health"}:
        operation = schema["paths"][path]["get"]
        if "422" not in operation["responses"]:
            continue
        validation_schema = operation["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert validation_schema["$ref"].endswith("/ErrorResponse")


def test_openapi_exposes_targeted_training_and_shadow_contracts(tmp_path: Path) -> None:
    schema = make_research_app(tmp_path).openapi()
    components = schema["components"]["schemas"]

    assert components["ResearchEvidenceScope"]["enum"] == [
        "verified",
        "challenger",
        "shadow",
    ]
    assert set(components["UpdateOperationStatus"]["enum"]) >= {
        "cancel_requested",
        "cancelled",
    }
    request = components["CandidateTrainingRequest"]["properties"]
    assert request["trigger"]["default"] == "manual"
    assert set(request["trigger"]["enum"]) == {"manual", "scheduled", "drift"}
