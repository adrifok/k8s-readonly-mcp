import hmac
import os

import uvicorn
from kubernetes import client, config
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("k8s-readonly")
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)


@mcp.tool(annotations=RO)
def list_pods(namespace: str = "default") -> list[dict]:
    """List pods in a namespace with phase, readiness, restarts and node."""
    result = []
    for p in client.CoreV1Api().list_namespaced_pod(namespace).items:
        cs = p.status.container_statuses or []
        result.append({
            "name": p.metadata.name,
            "phase": p.status.phase,
            "ready": bool(cs) and all(c.ready for c in cs),
            "restarts": sum(c.restart_count for c in cs),
            "node": p.spec.node_name,
        })
    return result


@mcp.tool(annotations=RO)
def list_deployments(namespace: str = "default") -> list[dict]:
    """List deployments with ready/desired replicas and container images."""
    return [
        {
            "name": d.metadata.name,
            "ready": f"{d.status.ready_replicas or 0}/{d.spec.replicas}",
            "images": [c.image for c in d.spec.template.spec.containers],
        }
        for d in client.AppsV1Api().list_namespaced_deployment(namespace).items
    ]


@mcp.tool(annotations=RO)
def rollout_status(name: str, namespace: str = "default") -> dict:
    """Report whether a deployment rollout is complete, with its conditions."""
    d = client.AppsV1Api().read_namespaced_deployment(name, namespace)
    s, desired = d.status, d.spec.replicas
    return {
        "complete": s.observed_generation == d.metadata.generation
        and (s.updated_replicas or 0) == desired
        and (s.available_replicas or 0) == desired
        and (s.replicas or 0) == (s.updated_replicas or 0),  # old ReplicaSet pods gone, same rule as kubectl
        "desired": desired,
        "updated": s.updated_replicas or 0,
        "available": s.available_replicas or 0,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in s.conditions or []
        ],
    }


@mcp.tool(annotations=RO)
def list_events(namespace: str = "default", warnings_only: bool = False, limit: int = 20) -> list[dict]:
    """List the most recent events in a namespace, newest first."""
    events = client.CoreV1Api().list_namespaced_event(
        namespace, field_selector="type=Warning" if warnings_only else None
    ).items
    events.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
    return [
        {
            "type": e.type,
            "reason": e.reason,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "message": e.message,
            "count": e.count,
        }
        for e in events[: max(1, min(limit, 100))]
    ]


def require_token(app, token: str):
    """Reject HTTP requests without the expected bearer token."""
    expected = f"Bearer {token}".encode()

    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, expected):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)  # lifespan events pass through untouched

    return wrapped


def main() -> None:
    try:
        config.load_incluster_config()  # ServiceAccount token when running inside a pod
    except config.ConfigException:
        config.load_kube_config()
    if os.getenv("MCP_TRANSPORT", "stdio") != "http":
        mcp.run()
        return
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        raise SystemExit("MCP_AUTH_TOKEN is required for http transport")
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    uvicorn.run(require_token(app, token), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
