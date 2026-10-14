from html.parser import HTMLParser

from fastapi.testclient import TestClient

from material_allocation_copilot.api.app import create_app
from material_allocation_copilot.api.auth import CredentialStore
from material_allocation_copilot.config import AppConfig, DeploymentEnvironment


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.labels: list[str] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if element_id := values.get("id"):
            self.ids.append(element_id)
        if tag == "label" and (target := values.get("for")):
            self.labels.append(target)
        if tag == "script" and (source := values.get("src")):
            self.scripts.append(source)
        if tag == "link" and values.get("rel") == "stylesheet" and (href := values.get("href")):
            self.stylesheets.append(href)


def client() -> TestClient:
    config = AppConfig(environment=DeploymentEnvironment.TEST, database_url="sqlite://")
    return TestClient(
        create_app(
            config,
            uow_factory=lambda: None,
            credentials=CredentialStore(()),
        )
    )


def test_simple_workflow_is_packaged_and_removed_workbench_stays_unavailable() -> None:
    api = client()
    page = api.get("/")
    script = api.get("/simple/assets/simple.js")
    styles = api.get("/simple/assets/simple.css")

    assert all(response.status_code == 200 for response in (page, script, styles))
    assert api.get("/workbench").status_code == 404
    assert api.get("/workbench/assets/app.js").status_code == 404
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["content-security-policy"].startswith("default-src 'self'")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert script.headers["content-type"].startswith("text/javascript")
    assert styles.headers["content-type"].startswith("text/css")

    schema = api.get("/openapi.json").json()
    assert schema["info"]["version"] == "1.4.0"
    assert len(schema["paths"]) == 41
    assert "/" not in schema["paths"]
    assert "/workbench" not in schema["paths"]


def test_simple_workflow_has_three_clear_steps_and_external_assets() -> None:
    page = client().get("/").text
    parser = PageParser()
    parser.feed(page)

    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.scripts) == {"/simple/assets/simple.js"}
    assert set(parser.stylesheets) == {"/simple/assets/simple.css"}
    assert not any(token in page for token in ("<script>", "style=", "onclick="))
    for control_id in (
        "material-file",
        "structure-canvas",
        "grid-x",
        "grid-y",
        "grid-z",
        "preference-prompt",
        "model-interpretation",
        "run-allocation",
        "bearer-token-simple",
    ):
        assert control_id in parser.ids
    assert "木料录入" in page
    assert "描述结构" in page
    assert "提示词输入" in page
    assert "减少表面节疤" in page
    assert "/workbench" not in page
    assert "结构认证" not in page
    assert "我理解：" not in page
    assert 'role="status"' in page
    assert 'aria-live="polite"' in page


def test_simple_workflow_uses_model_api_and_keeps_credentials_ephemeral() -> None:
    script = client().get("/simple/assets/simple.js").text

    for endpoint in (
        "/api/v1/me",
        "/inventory/import",
        "/preference-interpretations",
        "/requirements/confirm",
        "/tasks",
        "/ranked-plans",
    ):
        assert endpoint in script
    assert 'bearerToken: ""' in script
    assert 'headers.set("Authorization", `Bearer ${state.bearerToken}`)' in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script
    assert ".innerHTML" not in script
    assert ".textContent" in script
    assert "interpretPrompt" not in script
    assert "weight-waste-simple" not in script
    assert "surfaceKnotsFromRow" in script
    assert "surfaceKnotSummary" in script
    assert "整板节疤记录" in script
    assert "minimize_surface_defects" in script
    assert (
        'structural_use_status: raw.structural_use_status || (demo ? "eligible" : "not_evaluable")'
    ) in script
    assert "maximum_edge_count" in script


def test_security_headers_also_cover_api_failures() -> None:
    response = client().get("/api/v1/projects")

    assert response.status_code == 401
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
