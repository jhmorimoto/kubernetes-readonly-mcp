from __future__ import annotations

from pathlib import Path
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient

KUBECONFIG_DIRECTORY = Path.home() / ".kube" / "configs"


class ClusterError(RuntimeError):
    """An error that can be safely returned by an MCP tool."""


def _config_files(directory: Path) -> list[Path]:
    if not directory.exists():
        raise ClusterError(f"Kubeconfig directory not found: {directory}")
    return sorted(path for path in directory.iterdir() if path.is_file())


def discover_clusters(
    directory: Path = KUBECONFIG_DIRECTORY,
) -> tuple[list[dict[str, str]], list[str]]:
    """Discover contexts and kubeconfig files skipped due to duplicate contexts."""
    clusters: list[dict[str, str]] = []
    seen_contexts: dict[str, Path] = {}
    invalid_files: list[str] = []
    duplicate_files: list[str] = []

    for config_file in _config_files(directory):
        try:
            contexts, active_context = config.list_kube_config_contexts(
                config_file=str(config_file)
            )
        except Exception:
            invalid_files.append(config_file.name)
            continue

        for context in contexts:
            context_name = context["name"]
            if context_name in seen_contexts:
                if config_file.name not in duplicate_files:
                    duplicate_files.append(config_file.name)
                continue
            seen_contexts[context_name] = config_file
            clusters.append(
                {
                    "context": context_name,
                    "kubeconfig": config_file.name,
                    "cluster": context["context"].get("cluster", ""),
                    "user": context["context"].get("user", ""),
                    "active_in_file": str(
                        active_context is not None and active_context["name"] == context_name
                    ).lower(),
                }
            )

    if not clusters:
        detail = f" Invalid files: {', '.join(invalid_files)}." if invalid_files else ""
        raise ClusterError(f"No Kubernetes contexts found in {directory}.{detail}")
    return clusters, duplicate_files


def available_clusters(directory: Path = KUBECONFIG_DIRECTORY) -> list[dict[str, str]]:
    """Discover unique Kubernetes contexts from every regular config file."""
    clusters, _ = discover_clusters(directory)
    return clusters


def _config_for_context(context_name: str) -> Path:
    for cluster in available_clusters():
        if cluster["context"] == context_name:
            return KUBECONFIG_DIRECTORY / cluster["kubeconfig"]
    raise ClusterError(f"Unknown cluster context: '{context_name}'. Use list_clusters first.")


def api_clients(context_name: str) -> tuple[client.CoreV1Api, client.AppsV1Api, client.RbacAuthorizationV1Api, client.StorageV1Api, client.VersionApi]:
    """Return Kubernetes API clients configured for one explicitly selected context."""
    config_file = _config_for_context(context_name)
    api_client = config.new_client_from_config(
        config_file=str(config_file), context=context_name
    )
    return (
        client.CoreV1Api(api_client),
        client.AppsV1Api(api_client),
        client.RbacAuthorizationV1Api(api_client),
        client.StorageV1Api(api_client),
        client.VersionApi(api_client),
    )


def dynamic_client(context_name: str) -> DynamicClient:
    """Return a Kubernetes dynamic client configured for one explicitly selected context."""
    config_file = _config_for_context(context_name)
    api_client = config.new_client_from_config(
        config_file=str(config_file), context=context_name
    )
    return DynamicClient(api_client)


def kubernetes_error(error: Exception) -> ClusterError:
    if isinstance(error, ApiException):
        if error.status == 401:
            return ClusterError("Authentication failed for this cluster context.")
        if error.status == 403:
            return ClusterError("Access denied by Kubernetes RBAC for this query.")
        if error.status == 404:
            return ClusterError("The requested Kubernetes resource was not found.")
        return ClusterError(f"Kubernetes API error ({error.status}): {error.reason}")
    return ClusterError(f"Unable to query Kubernetes: {error}")


def metadata(resource: Any) -> dict[str, Any]:
    value = resource.metadata
    return {
        "name": value.name,
        "namespace": value.namespace,
        "creation_timestamp": str(value.creation_timestamp) if value.creation_timestamp else None,
        "labels": value.labels or {},
        "annotations": value.annotations or {},
    }