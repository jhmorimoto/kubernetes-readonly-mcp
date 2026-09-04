from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes.dynamic.exceptions import ResourceNotFoundError, ResourceNotUniqueError
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from k8s_readonly_mcp.cluster import (
    ClusterError,
    api_clients,
    available_clusters,
    discover_clusters,
    dynamic_client,
    kubernetes_error,
    metadata,
)

mcp = MCPServer(
    "kubernetes-readonly",
    title="Kubernetes Read-Only Diagnostics",
    description="Diagnose Kubernetes clusters without modifying cluster resources.",
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _query(operation: Any) -> Any:
    try:
        return operation()
    except ClusterError:
        raise
    except Exception as error:
        raise kubernetes_error(error) from None


def _items(resources: Any) -> list[dict[str, Any]]:
    return [metadata(resource) for resource in resources.items]


def _resource_to_dict(resource: Any) -> dict[str, Any]:
    if hasattr(resource, "to_dict"):
        return resource.to_dict()
    if isinstance(resource, dict):
        return resource
    return dict(resource)


def _dynamic_resource(dynamic: Any, api_version: str, kind: str) -> Any:
    try:
        return dynamic.resources.get(api_version=api_version, kind=kind)
    except ResourceNotFoundError as error:
        raise ClusterError(f"Kubernetes resource kind not found: {api_version}/{kind}.") from error
    except ResourceNotUniqueError as error:
        raise ClusterError(f"Kubernetes resource kind is ambiguous: {api_version}/{kind}. Use list_api_resources first.") from error


def _get_namespaced_argument(resource: Any, namespace: str | None) -> dict[str, str]:
    if getattr(resource, "namespaced", False):
        if not namespace:
            raise ClusterError("namespace is required for this namespaced resource.")
        return {"namespace": namespace}
    return {}


def _list_namespaced_argument(resource: Any, namespace: str | None) -> dict[str, str]:
    if getattr(resource, "namespaced", False) and namespace:
        return {"namespace": namespace}
    return {}


def _selector_arguments(label_selector: str | None, field_selector: str | None) -> dict[str, str]:
    arguments = {}
    if label_selector:
        arguments["label_selector"] = label_selector
    if field_selector:
        arguments["field_selector"] = field_selector
    return arguments


@mcp.tool(annotations=READ_ONLY)
def list_clusters() -> dict[str, Any]:
    """List contexts and kubeconfig files skipped due to duplicate contexts."""
    clusters, duplicate_kubeconfig_files = _query(discover_clusters)
    return {
        "clusters": clusters,
        "duplicate_kubeconfig_files": duplicate_kubeconfig_files,
    }


@mcp.tool(annotations=READ_ONLY)
def list_api_resources(cluster: str, api_version: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    """Discover Kubernetes API resources, including CRDs, that can be queried."""
    dynamic = _query(lambda: dynamic_client(cluster))
    resources = _query(lambda: dynamic.resources.search(api_version=api_version, kind=kind))
    return [
        {
            "api_version": resource.group_version,
            "kind": resource.kind,
            "name": resource.name,
            "namespaced": resource.namespaced,
            "verbs": resource.verbs or [],
            "short_names": resource.short_names or [],
            "categories": resource.categories or [],
        }
        for resource in resources
        if "get" in (resource.verbs or []) or "list" in (resource.verbs or [])
    ]


@mcp.tool(annotations=READ_ONLY)
def list_crds(cluster: str) -> list[dict[str, Any]]:
    """List all installed CustomResourceDefinitions and their served versions."""
    dynamic = _query(lambda: dynamic_client(cluster))
    resource = _dynamic_resource(dynamic, "apiextensions.k8s.io/v1", "CustomResourceDefinition")
    if "list" not in (resource.verbs or []):
        raise ClusterError("Kubernetes resource is not listable: apiextensions.k8s.io/v1/CustomResourceDefinition.")
    crds = _query(resource.get).items
    return [
        {
            **metadata(crd),
            "group": crd.spec.group,
            "kind": crd.spec.names.kind,
            "plural": crd.spec.names.plural,
            "scope": crd.spec.scope,
            "versions": [
                {"name": version.name, "served": version.served, "storage": version.storage}
                for version in crd.spec.versions
            ],
        }
        for crd in crds
    ]


@mcp.tool(annotations=READ_ONLY)
def list_resources(
    cluster: str,
    api_version: str,
    kind: str,
    namespace: str | None = None,
    label_selector: str | None = None,
    field_selector: str | None = None,
    limit: int | None = None,
    continue_token: str | None = None,
) -> dict[str, Any]:
    """List any Kubernetes resource kind using apiVersion/kind discovery."""
    if limit is not None and not 1 <= limit <= 5_000:
        raise ClusterError("limit must be between 1 and 5000.")
    dynamic = _query(lambda: dynamic_client(cluster))
    resource = _dynamic_resource(dynamic, api_version, kind)
    if "list" not in (resource.verbs or []):
        raise ClusterError(f"Kubernetes resource is not listable: {api_version}/{kind}.")
    arguments: dict[str, Any] = {
        **_list_namespaced_argument(resource, namespace),
        **_selector_arguments(label_selector, field_selector),
    }
    if limit is not None:
        arguments["limit"] = limit
    if continue_token:
        arguments["_continue"] = continue_token
    result = _resource_to_dict(_query(lambda: resource.get(**arguments)))
    metadata_value = result.get("metadata", {})
    return {
        "api_version": result.get("apiVersion"),
        "kind": result.get("kind"),
        "resource_version": metadata_value.get("resourceVersion"),
        "continue_token": metadata_value.get("continue"),
        "items": result.get("items", []),
    }


@mcp.tool(annotations=READ_ONLY)
def get_resource(cluster: str, api_version: str, kind: str, name: str, namespace: str | None = None) -> dict[str, Any]:
    """Get any Kubernetes resource object by apiVersion, kind and name."""
    dynamic = _query(lambda: dynamic_client(cluster))
    resource = _dynamic_resource(dynamic, api_version, kind)
    if "get" not in (resource.verbs or []):
        raise ClusterError(f"Kubernetes resource is not readable by name: {api_version}/{kind}.")
    return _resource_to_dict(_query(lambda: resource.get(name=name, **_get_namespaced_argument(resource, namespace))))


@mcp.tool(annotations=READ_ONLY)
def get_cluster_health(cluster: str) -> dict[str, Any]:
    """Get Kubernetes version and a summary of node readiness for one context."""
    core, _, _, _, version = _query(lambda: api_clients(cluster))
    server_version = _query(version.get_code)
    nodes = _query(core.list_node).items
    ready_nodes = sum(
        any(condition.type == "Ready" and condition.status == "True" for condition in (node.status.conditions or []))
        for node in nodes
    )
    return {
        "cluster": cluster,
        "kubernetes_version": server_version.git_version,
        "platform": server_version.platform,
        "nodes_total": len(nodes),
        "nodes_ready": ready_nodes,
        "nodes_not_ready": len(nodes) - ready_nodes,
    }


@mcp.tool(annotations=READ_ONLY)
def list_nodes(cluster: str) -> list[dict[str, Any]]:
    """List nodes with readiness, roles, version and pressure conditions."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    nodes = _query(core.list_node).items
    return [
        {
            **metadata(node),
            "ready": next((condition.status for condition in (node.status.conditions or []) if condition.type == "Ready"), "Unknown"),
            "conditions": [
                {"type": condition.type, "status": condition.status, "reason": condition.reason}
                for condition in (node.status.conditions or [])
                if condition.status != "False" or condition.type == "Ready"
            ],
            "kubelet_version": node.status.node_info.kubelet_version,
        }
        for node in nodes
    ]


@mcp.tool(annotations=READ_ONLY)
def list_namespaces(cluster: str) -> list[dict[str, Any]]:
    """List namespaces and their phases."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    return [{**metadata(item), "phase": item.status.phase} for item in _query(core.list_namespace).items]


@mcp.tool(annotations=READ_ONLY)
def list_pods(cluster: str, namespace: str | None = None, problems_only: bool = False) -> list[dict[str, Any]]:
    """List Pods, optionally restricting results to Pods with failure signals."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    pods = _query(lambda: core.list_namespaced_pod(namespace) if namespace else core.list_pod_for_all_namespaces()).items
    result = [_pod_summary(pod) for pod in pods]
    return [pod for pod in result if pod["problem"]] if problems_only else result


def _pod_summary(pod: Any) -> dict[str, Any]:
    statuses = pod.status.container_statuses or []
    problems = []
    for status in statuses:
        waiting = status.state.waiting if status.state else None
        terminated = status.state.terminated if status.state else None
        if waiting and waiting.reason:
            problems.append(f"{status.name}: {waiting.reason}")
        if terminated and terminated.exit_code != 0:
            problems.append(f"{status.name}: {terminated.reason or 'Terminated'} ({terminated.exit_code})")
        if status.restart_count:
            problems.append(f"{status.name}: restarts={status.restart_count}")
    phase = pod.status.phase or "Unknown"
    problem = phase in {"Failed", "Unknown"} or bool(problems)
    return {
        **metadata(pod),
        "phase": phase,
        "node": pod.spec.node_name,
        "pod_ip": pod.status.pod_ip,
        "containers_ready": sum(status.ready for status in statuses),
        "containers_total": len(statuses),
        "problem": problem,
        "problem_details": problems,
    }


@mcp.tool(annotations=READ_ONLY)
def describe_pod(cluster: str, namespace: str, name: str) -> dict[str, Any]:
    """Return detailed status, container state and conditions for a Pod."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    pod = _query(lambda: core.read_namespaced_pod(name, namespace))
    return {
        **_pod_summary(pod),
        "conditions": [
            {"type": condition.type, "status": condition.status, "reason": condition.reason, "message": condition.message}
            for condition in (pod.status.conditions or [])
        ],
        "containers": [
            {
                "name": status.name,
                "ready": status.ready,
                "restart_count": status.restart_count,
                "state": status.state.to_dict() if status.state else {},
            }
            for status in (pod.status.container_statuses or [])
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def get_pod_logs(cluster: str, namespace: str, name: str, container: str | None = None, tail_lines: int = 200, previous: bool = False) -> str:
    """Read a bounded tail of container logs. Logs may contain sensitive application data."""
    if not 1 <= tail_lines <= 2_000:
        raise ClusterError("tail_lines must be between 1 and 2000.")
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    return _query(lambda: core.read_namespaced_pod_log(name, namespace, container=container, tail_lines=tail_lines, previous=previous, timestamps=True))


def _workloads(cluster: str, namespace: str | None, kind: str) -> list[dict[str, Any]]:
    _, apps, _, _, _ = _query(lambda: api_clients(cluster))
    method = getattr(apps, f"list_namespaced_{kind}" if namespace else f"list_{kind}_for_all_namespaces")
    resources = _query(lambda: method(namespace) if namespace else method())
    return [
        {
            **metadata(item),
            "desired_replicas": item.spec.replicas,
            "ready_replicas": item.status.ready_replicas or 0,
            "available_replicas": getattr(item.status, "available_replicas", 0) or 0,
            "updated_replicas": item.status.updated_replicas or 0,
        }
        for item in resources.items
    ]


@mcp.tool(annotations=READ_ONLY)
def list_deployments(cluster: str, namespace: str | None = None) -> list[dict[str, Any]]:
    """List Deployments and replica readiness."""
    return _workloads(cluster, namespace, "deployment")


@mcp.tool(annotations=READ_ONLY)
def list_statefulsets(cluster: str, namespace: str | None = None) -> list[dict[str, Any]]:
    """List StatefulSets and replica readiness."""
    return _workloads(cluster, namespace, "stateful_set")


@mcp.tool(annotations=READ_ONLY)
def list_events(cluster: str, namespace: str | None = None, hours: int = 24) -> list[dict[str, Any]]:
    """List recent warning and normal Kubernetes events, newest first."""
    if not 1 <= hours <= 168:
        raise ClusterError("hours must be between 1 and 168.")
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    events = _query(lambda: core.list_namespaced_event(namespace) if namespace else core.list_event_for_all_namespaces()).items
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    result = []
    for event in events:
        event_time = event.event_time or event.last_timestamp or event.metadata.creation_timestamp
        if event_time and event_time < cutoff:
            continue
        result.append({
            **metadata(event), "type": event.type, "reason": event.reason,
            "message": event.message, "count": event.count,
            "involved_object": f"{event.involved_object.kind}/{event.involved_object.name}",
            "event_time": str(event_time) if event_time else None,
        })
    return sorted(result, key=lambda event: event["event_time"] or "", reverse=True)


@mcp.tool(annotations=READ_ONLY)
def list_storage(cluster: str, namespace: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """List PersistentVolumeClaims, PersistentVolumes and StorageClasses."""
    core, _, _, storage, _ = _query(lambda: api_clients(cluster))
    pvcs = _query(lambda: core.list_namespaced_persistent_volume_claim(namespace) if namespace else core.list_persistent_volume_claim_for_all_namespaces())
    pvs = _query(core.list_persistent_volume)
    classes = _query(storage.list_storage_class)
    return {
        "persistent_volume_claims": [{**metadata(item), "phase": item.status.phase, "volume": item.spec.volume_name, "storage_class": item.spec.storage_class_name} for item in pvcs.items],
        "persistent_volumes": [{**metadata(item), "phase": item.status.phase, "claim": f"{item.spec.claim_ref.namespace}/{item.spec.claim_ref.name}" if item.spec.claim_ref else None, "storage_class": item.spec.storage_class_name} for item in pvs.items],
        "storage_classes": [{**metadata(item), "provisioner": item.provisioner, "default": item.metadata.annotations.get("storageclass.kubernetes.io/is-default-class") == "true" if item.metadata.annotations else False} for item in classes.items],
    }


@mcp.tool(annotations=READ_ONLY)
def list_rbac(cluster: str, namespace: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """List Roles, RoleBindings, ClusterRoles and ClusterRoleBindings as metadata."""
    _, _, rbac, _, _ = _query(lambda: api_clients(cluster))
    roles = _query(lambda: rbac.list_namespaced_role(namespace) if namespace else rbac.list_role_for_all_namespaces())
    bindings = _query(lambda: rbac.list_namespaced_role_binding(namespace) if namespace else rbac.list_role_binding_for_all_namespaces())
    return {
        "roles": _items(roles),
        "role_bindings": _items(bindings),
        "cluster_roles": _items(_query(rbac.list_cluster_role)),
        "cluster_role_bindings": _items(_query(rbac.list_cluster_role_binding)),
    }


@mcp.tool(annotations=READ_ONLY)
def get_configmap(cluster: str, namespace: str, name: str) -> dict[str, Any]:
    """Read a ConfigMap including its data. Review returned data for sensitive values."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    configmap = _query(lambda: core.read_namespaced_config_map(name, namespace))
    return {**metadata(configmap), "data": configmap.data or {}, "binary_data_keys": sorted((configmap.binary_data or {}).keys())}


@mcp.tool(annotations=READ_ONLY)
def list_secrets(cluster: str, namespace: str | None = None) -> list[dict[str, Any]]:
    """List Secret metadata only; secret values are never returned."""
    core, _, _, _, _ = _query(lambda: api_clients(cluster))
    secrets = _query(lambda: core.list_namespaced_secret(namespace) if namespace else core.list_secret_for_all_namespaces())
    return [{**metadata(secret), "type": secret.type, "data_keys": sorted((secret.data or {}).keys())} for secret in secrets.items]