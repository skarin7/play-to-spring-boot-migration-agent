"""tools/endpoint_diff.py tests (M6 Task 10, T5): build_probes, capture,
diff_responses. All pure/deterministic -- no subprocess, no HTTP, no LLM.
"""

import json

from agent.tools.endpoint_diff import HttpResponse, build_probes, capture, diff_responses


def _route(method="GET", path="/users", controller="UserController", action="list", params=""):
    return {"method": method, "path": path, "controller": controller, "action": action, "params": params}


# ----------------------------------------------------------------------
# build_probes
# ----------------------------------------------------------------------


def test_build_probes_parameterless_get_is_probed():
    probes, unproved = build_probes([_route()])
    assert len(probes) == 1
    assert probes[0].resolved_path == "/users"
    assert unproved == []


def test_build_probes_post_disabled_by_default():
    probes, unproved = build_probes([_route(method="POST", path="/users")])
    assert probes == []
    assert len(unproved) == 1
    assert "request body" in unproved[0]["reason"]


def test_build_probes_post_enabled_with_include_mutating():
    probes, unproved = build_probes([_route(method="POST", path="/users")], include_mutating=True)
    assert len(probes) == 1
    assert unproved == []


def test_build_probes_path_param_without_sample_value_is_unproved():
    probes, unproved = build_probes([_route(path="/users/:id")])
    assert probes == []
    assert len(unproved) == 1
    assert "id" in unproved[0]["reason"]


def test_build_probes_path_param_with_sample_value_resolved():
    probes, unproved = build_probes([_route(path="/users/:id")], path_params={"id": "42"})
    assert len(probes) == 1
    assert probes[0].resolved_path == "/users/42"
    assert unproved == []


def test_build_probes_multiple_path_params():
    probes, _ = build_probes(
        [_route(path="/orgs/:orgId/users/:id")], path_params={"orgId": "acme", "id": "7"}
    )
    assert probes[0].resolved_path == "/orgs/acme/users/7"


def test_build_probes_mixed_routes():
    routes = [_route(path="/health"), _route(method="DELETE", path="/users/:id")]
    probes, unproved = build_probes(routes)
    assert len(probes) == 1
    assert probes[0].resolved_path == "/health"
    assert len(unproved) == 1


# ----------------------------------------------------------------------
# capture
# ----------------------------------------------------------------------


def test_capture_calls_http_get_per_probe():
    probes, _ = build_probes([_route(path="/health"), _route(path="/status")])
    seen_urls = []

    def http_get(url):
        seen_urls.append(url)
        return HttpResponse(status=200, body="{}")

    results = capture("http://localhost:8080", probes, http_get)

    assert seen_urls == ["http://localhost:8080/health", "http://localhost:8080/status"]
    assert set(results) == {"/health", "/status"}


def test_capture_strips_trailing_slash_from_base_url():
    probes, _ = build_probes([_route(path="/health")])
    seen = []
    capture("http://localhost:8080/", probes, lambda url: seen.append(url) or HttpResponse(200, "{}"))
    assert seen == ["http://localhost:8080/health"]


def test_capture_request_exception_recorded_not_dropped():
    """A dropped probe would make its absence from the diff look like
    agreement -- must be recorded with a distinguishable status instead."""
    probes, _ = build_probes([_route(path="/health")])

    def failing_get(url):
        raise ConnectionError("connection refused")

    results = capture("http://localhost:8080", probes, failing_get)

    assert results["/health"].status == -1
    assert "connection refused" in results["/health"].body


# ----------------------------------------------------------------------
# diff_responses
# ----------------------------------------------------------------------


def test_diff_identical_responses_no_findings():
    before = {"/health": HttpResponse(200, '{"status": "ok"}')}
    after = {"/health": HttpResponse(200, '{"status": "ok"}')}
    assert diff_responses(before, after) == []


def test_diff_status_changed():
    before = {"/health": HttpResponse(200, "{}")}
    after = {"/health": HttpResponse(500, "{}")}
    findings = diff_responses(before, after)
    assert len(findings) == 1
    assert findings[0]["kind"] == "status_changed"


def test_diff_field_missing_in_after():
    before = {"/u": HttpResponse(200, json.dumps({"name": "x", "age": 5}))}
    after = {"/u": HttpResponse(200, json.dumps({"name": "x"}))}
    findings = diff_responses(before, after)
    assert any(f["kind"] == "field_missing_in_after" and f["path"] == "/u.age" for f in findings)


def test_diff_field_added_in_after():
    before = {"/u": HttpResponse(200, json.dumps({"name": "x"}))}
    after = {"/u": HttpResponse(200, json.dumps({"name": "x", "extra": 1}))}
    findings = diff_responses(before, after)
    assert any(f["kind"] == "field_added_in_after" and f["path"] == "/u.extra" for f in findings)


def test_diff_value_changed():
    before = {"/u": HttpResponse(200, json.dumps({"name": "old"}))}
    after = {"/u": HttpResponse(200, json.dumps({"name": "new"}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.name", "kind": "value_changed", "before": "old", "after": "new"}]


def test_diff_volatile_id_field_presence_and_type_only_not_equality():
    before = {"/u": HttpResponse(200, json.dumps({"id": 1, "name": "x"}))}
    after = {"/u": HttpResponse(200, json.dumps({"id": 999, "name": "x"}))}
    # Different id VALUES but same type -- must not be a finding.
    findings = diff_responses(before, after)
    assert findings == []


def test_diff_volatile_timestamp_field_type_change_is_a_finding():
    before = {"/u": HttpResponse(200, json.dumps({"createdAt": "2026-01-01T00:00:00Z"}))}
    after = {"/u": HttpResponse(200, json.dumps({"createdAt": None}))}
    findings = diff_responses(before, after)
    assert len(findings) == 1
    assert findings[0]["kind"] == "volatile_field_type_changed"


def test_diff_duration_field_is_volatile():
    before = {"/u": HttpResponse(200, json.dumps({"durationMs": 42}))}
    after = {"/u": HttpResponse(200, json.dumps({"durationMs": 999}))}
    assert diff_responses(before, after) == []


def test_diff_field_ordering_never_a_difference():
    before = {"/u": HttpResponse(200, json.dumps({"a": 1, "b": 2}))}
    after = {"/u": HttpResponse(200, '{"b": 2, "a": 1}')}
    assert diff_responses(before, after) == []


def test_diff_array_length_changed():
    before = {"/u": HttpResponse(200, json.dumps({"items": [1, 2, 3]}))}
    after = {"/u": HttpResponse(200, json.dumps({"items": [1, 2]}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.items", "kind": "array_length_changed", "before": 3, "after": 2}]


def test_diff_array_element_changed():
    before = {"/u": HttpResponse(200, json.dumps({"items": [1, 2, 3]}))}
    after = {"/u": HttpResponse(200, json.dumps({"items": [1, 9, 3]}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.items[1]", "kind": "value_changed", "before": 2, "after": 9}]


def test_diff_nested_object():
    before = {"/u": HttpResponse(200, json.dumps({"address": {"city": "NYC"}}))}
    after = {"/u": HttpResponse(200, json.dumps({"address": {"city": "LA"}}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.address.city", "kind": "value_changed", "before": "NYC", "after": "LA"}]


def test_diff_non_json_body_changed():
    before = {"/text": HttpResponse(200, "hello")}
    after = {"/text": HttpResponse(200, "goodbye")}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/text", "kind": "body_changed_non_json"}]


def test_diff_non_json_body_identical_no_finding():
    before = {"/text": HttpResponse(200, "hello")}
    after = {"/text": HttpResponse(200, "hello")}
    assert diff_responses(before, after) == []


def test_diff_content_type_changed_json_to_text():
    before = {"/x": HttpResponse(200, json.dumps({"a": 1}))}
    after = {"/x": HttpResponse(200, "not json anymore")}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/x", "kind": "body_content_type_changed"}]


def test_diff_probe_missing_on_one_side():
    before = {"/a": HttpResponse(200, "{}"), "/b": HttpResponse(200, "{}")}
    after = {"/a": HttpResponse(200, "{}")}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/b", "kind": "probe_missing_on_one_side"}]


def test_diff_both_sides_unreachable_recorded_not_deep_diffed():
    before = {"/x": HttpResponse(-1, "connection refused")}
    after = {"/x": HttpResponse(-1, "timeout")}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/x", "kind": "both_sides_unreachable"}]


def test_diff_field_named_grid_is_not_treated_as_volatile_id():
    """Regression: "grid"/"valid" end in the letters "id" but are not an "id"
    field -- a naive suffix match on "id" would false-positive on these."""
    before = {"/u": HttpResponse(200, json.dumps({"grid": "A1"}))}
    after = {"/u": HttpResponse(200, json.dumps({"grid": "B2"}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.grid", "kind": "value_changed", "before": "A1", "after": "B2"}]


def test_diff_field_named_valid_is_not_treated_as_volatile_id():
    before = {"/u": HttpResponse(200, json.dumps({"valid": True}))}
    after = {"/u": HttpResponse(200, json.dumps({"valid": False}))}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/u.valid", "kind": "value_changed", "before": True, "after": False}]


def test_diff_multiple_paths_sorted_order():
    before = {"/b": HttpResponse(200, "one"), "/a": HttpResponse(200, "one")}
    after = {"/b": HttpResponse(200, "two"), "/a": HttpResponse(200, "one")}
    findings = diff_responses(before, after)
    assert findings == [{"path": "/b", "kind": "body_changed_non_json"}]
