from ingestion.domain.models import GraphRecord, GraphRelationship, UiObservation
from ingestion.extractor.code.implementation.typescript_code_extractor import TypeScriptCodeExtractor
from ingestion.mapping.implementation.nextjs_route_mapping import NextJsRouteMappingResolver
from ingestion.mapping.route_observation import observed_route_relationships


def test_code_extractor_models_checkout_route_and_query_parameter_names():
    path = "src/app/checkout/page.tsx"
    source = """export default function CheckoutPage(props: {
      searchParams: Promise<{ checkout?: string; order?: string }>;
    }) {}
    """

    nodes, relationships = TypeScriptCodeExtractor._route_graph((path,), {path: source}, "revision-1")

    route = next(node for node in nodes if node.kind == "Route")
    parameters = {node.properties["name"]: node for node in nodes if node.kind == "RouteParameter"}
    assert route.properties["path"] == "/checkout"
    assert set(route.properties["query_parameter_names"].split(",")) == {"checkout", "order"}
    assert parameters["checkout"].properties["location"] == "query"
    assert parameters["checkout"].properties["required"] == "false"
    assert {edge.kind for edge in relationships} == {"DECLARES_ROUTE", "ACCEPTS_QUERY_PARAMETER"}


def test_code_extractor_resolves_named_search_parameter_types():
    source = """type SearchParams = {
      query?: string | string[];
      cursor?: string | string[];
      direction?: string;
      sort?: string;
    };
    export default function SearchPage(props: {
      searchParams: Promise<SearchParams>;
    }) {}
    """

    parameters = TypeScriptCodeExtractor._query_parameters(source)

    assert [name for name, _, _ in parameters] == ["query", "cursor", "direction", "sort"]
    assert all(not required for _, required, _ in parameters)


def test_code_extractor_finds_query_keys_used_by_imported_client_components():
    page_path = "src/app/[channel]/login/page.tsx"
    component_path = "src/ui/components/login-form.tsx"
    nodes, _ = TypeScriptCodeExtractor._route_graph(
        (page_path, component_path),
        {
            page_path: 'import { LoginForm } from "@/ui/components/login-form"; export default LoginForm;',
            component_path: """
              "use client";
              import { useSearchParams } from "next/navigation";
              export function LoginForm() {
                const params = useSearchParams();
                return params.get("email") + params.get("token");
              }
            """,
        },
        "revision-1",
        (
            GraphRelationship(
                id="page-imports-login-form",
                source_id=f"file:{page_path}",
                target_id=f"file:{component_path}",
                kind="IMPORTS",
            ),
        ),
    )

    route = next(node for node in nodes if node.kind == "Route")
    parameters = {node.properties["name"] for node in nodes if node.kind == "RouteParameter"}

    assert route.properties["path"] == "/{channel}/login"
    assert parameters == {"email", "token"}


def test_observed_checkout_mapping_follows_imports_and_records_names_not_values():
    route_page = GraphRecord(
        id="file:src/app/checkout/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/checkout/page.tsx", "sha256": "page-sha"},
    )
    summary = GraphRecord(
        id="file:src/checkout/views/saleor-checkout/order-summary.tsx",
        kind="CodeFile",
        properties={
            "path": "src/checkout/views/saleor-checkout/order-summary.tsx",
            "sha256": "summary-sha",
        },
    )
    route = GraphRecord(
        id="route:revision:/checkout",
        kind="Route",
        properties={"path": "/checkout", "query_parameter_names": "checkout,order"},
    )
    parameter = GraphRecord(
        id="route-parameter:checkout",
        kind="RouteParameter",
        properties={"name": "checkout", "location": "query", "required": "false"},
    )
    page_to_route = GraphRelationship(
        id="declares-route",
        source_id=route_page.id,
        target_id=route.id,
        kind="DECLARES_ROUTE",
    )
    accepts_parameter = GraphRelationship(
        id="accepts-checkout",
        source_id=route.id,
        target_id=parameter.id,
        kind="ACCEPTS_QUERY_PARAMETER",
    )
    import_edge = GraphRelationship(
        id="imports-summary",
        source_id=route_page.id,
        target_id=summary.id,
        kind="IMPORTS",
    )
    observation = UiObservation(
        id="journey:checkout",
        url="https://store.example/checkout",
        query_parameter_names=("checkout",),
        evidence_ids=("browser:sha256",),
        content_sha256="sha256",
        page_title="Checkout",
        visible_text="Discount code Apply",
        http_status=200,
        captured=True,
        content_ready=True,
    )

    mapping = NextJsRouteMappingResolver().resolve(
        (route_page, summary, route, parameter),
        (observation,),
        (page_to_route, accepts_parameter, import_edge),
    )
    summary_mapping = next(item for item in mapping if item.source_id == summary.id)
    observed = observed_route_relationships((route,), (observation,))

    assert summary_mapping.target_id == observation.id
    assert summary_mapping.basis == "static_import_reachability"
    assert "code-edge:imports-summary" in summary_mapping.evidence_ids
    assert "browser:sha256" in summary_mapping.evidence_ids
    assert "code-sha256:summary-sha" in summary_mapping.evidence_ids
    assert observed[0].source_id == route.id
    assert observed[0].kind == "OBSERVED_AS"
    assert "checkout" in observed[0].properties["query_parameter_names"]


def test_route_mapper_rejects_unknown_checkout_query_parameter():
    page = GraphRecord(
        id="file:src/app/checkout/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/checkout/page.tsx", "sha256": "page-sha"},
    )
    route = GraphRecord(
        id="route:revision:/checkout",
        kind="Route",
        properties={"path": "/checkout"},
    )
    parameter = GraphRecord(
        id="route-parameter:checkout",
        kind="RouteParameter",
        properties={"name": "checkout", "location": "query"},
    )
    observation = UiObservation(
        id="page:unknown-query",
        url="https://store.example/checkout",
        query_parameter_names=("checkout_id_guess",),
        evidence_ids=("browser:sha256",),
        content_sha256="sha256",
        page_title="Checkout",
        visible_text="Page rendered",
        http_status=200,
        captured=True,
        content_ready=True,
    )

    relationships = (
        GraphRelationship(
            id="declares-route", source_id=page.id, target_id=route.id, kind="DECLARES_ROUTE"
        ),
        GraphRelationship(
            id="accepts-checkout",
            source_id=route.id,
            target_id=parameter.id,
            kind="ACCEPTS_QUERY_PARAMETER",
        ),
    )
    assert (
        NextJsRouteMappingResolver().resolve((page, route, parameter), (observation,), relationships)
        == ()
    )


def test_route_observation_matches_concrete_url_to_dynamic_route_template():
    route = GraphRecord(
        id="route:revision:products",
        kind="Route",
        properties={"path": "/{channel}/products"},
    )
    observation = UiObservation(
        id="page:products",
        url="https://store.example/default-channel/products",
        evidence_ids=("browser:products",),
        content_sha256="products-sha",
        page_title="Products",
        visible_text="All Products",
        http_status=200,
        captured=True,
        content_ready=True,
    )

    relationships = observed_route_relationships((route,), (observation,))

    assert len(relationships) == 1
    assert relationships[0].source_id == route.id
    assert relationships[0].target_id == observation.id
    assert relationships[0].kind == "OBSERVED_AS"
