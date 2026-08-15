"""tools/routes_parser.py tests: Play conf/routes parsing + Spring @*Mapping diff."""

from agent.tools.routes_parser import diff_routes, find_spring_mappings, parse_routes_file

ROUTES_TXT = """\
# Comment line, ignored
GET     /users                      controllers.UserController.list()
GET     /users/:id                  controllers.UserController.get(id: Long)
POST    /users                      controllers.UserController.create(request: Request)

DELETE  /users/:id                  controllers.UserController.delete(id: Long)
this line is malformed and has too many fields for a route
GET     /health                     controllers.HealthController.check
"""


def test_parse_routes_file_skips_blank_and_comment_lines(tmp_path):
    routes_path = tmp_path / "routes"
    routes_path.write_text(ROUTES_TXT, encoding="utf-8")
    routes = parse_routes_file(routes_path)
    # 4 well-formed routes; the "too many fields" line and the no-parens
    # "check" line are both malformed and skipped, not raised.
    assert len(routes) == 4
    assert routes[0] == {
        "method": "GET",
        "path": "/users",
        "controller": "controllers.UserController",
        "action": "list",
        "params": "",
    }
    assert routes[1] == {
        "method": "GET",
        "path": "/users/:id",
        "controller": "controllers.UserController",
        "action": "get",
        "params": "id: Long",
    }
    assert routes[3]["action"] == "delete"


def test_parse_routes_file_skips_malformed_lines(tmp_path):
    routes_path = tmp_path / "routes"
    routes_path.write_text(ROUTES_TXT, encoding="utf-8")
    routes = parse_routes_file(routes_path)
    actions = {r["action"] for r in routes}
    assert "check" not in actions  # no '(' -> malformed, skipped
    assert len(routes) == 4


SPRING_JAVA = """\
package controllers;

import org.springframework.web.bind.annotation.*;

@RestController
public class UserController {

    @GetMapping("/users")
    public ResponseEntity<List<User>> list() {
        return null;
    }

    @GetMapping("/users/:id")
    public ResponseEntity<User> get(@PathVariable Long id) {
        return null;
    }

    // no annotation on create() -> not mapped yet
    public ResponseEntity<User> create(Request request) {
        return null;
    }
}
"""


def test_find_spring_mappings_scans_annotations(tmp_path):
    spring_repo = tmp_path / "spring"
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True)
    (java_dir / "UserController.java").write_text(SPRING_JAVA, encoding="utf-8")

    mappings = find_spring_mappings(spring_repo)
    assert mappings["UserController.list"] == {"/users"}
    assert mappings["UserController.get"] == {"/users/:id"}
    assert "UserController.create" not in mappings


NESTED_CLASS_JAVA = """\
package controllers;

import org.springframework.web.bind.annotation.*;

@RestController
public class UserController {

    public static class CreateRequest {
        public String name;
    }

    @GetMapping("/users")
    public ResponseEntity<List<User>> list() {
        return null;
    }
}
"""


def test_find_spring_mappings_attributes_nested_class_method_to_outer_class(tmp_path):
    """Regression: a nested static DTO class declared before an annotated
    method must not shadow the enclosing controller class in the mapping key."""
    spring_repo = tmp_path / "spring"
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True)
    (java_dir / "UserController.java").write_text(NESTED_CLASS_JAVA, encoding="utf-8")

    mappings = find_spring_mappings(spring_repo)
    assert mappings["UserController.list"] == {"/users"}
    assert "CreateRequest.list" not in mappings


def test_diff_routes_splits_mapped_and_unmapped(tmp_path):
    routes_path = tmp_path / "routes"
    routes_path.write_text(ROUTES_TXT, encoding="utf-8")
    routes = parse_routes_file(routes_path)

    spring_repo = tmp_path / "spring"
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True)
    (java_dir / "UserController.java").write_text(SPRING_JAVA, encoding="utf-8")

    mappings = find_spring_mappings(spring_repo)
    mapped, unmapped = diff_routes(routes, mappings)

    mapped_actions = {(r["controller"], r["action"]) for r in mapped}
    unmapped_actions = {(r["controller"], r["action"]) for r in unmapped}

    assert ("controllers.UserController", "list") in mapped_actions
    assert ("controllers.UserController", "get") in mapped_actions
    assert ("controllers.UserController", "create") in unmapped_actions
    assert ("controllers.UserController", "delete") in unmapped_actions
    assert len(mapped) == 2
    assert len(unmapped) == 2
