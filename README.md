# Kubernetes Read-Only MCP

MCP server for diagnosing Kubernetes clusters without creating or changing cluster objects.

## Prerequisites

- `uv`
- One or more readable kubeconfig files in `~/.kube/configs`
- Kubernetes RBAC limited to read-only permissions, for defense in depth

The MCP identifier for a cluster is the Kubernetes context name declared inside a kubeconfig, not the file name. Use `list_clusters` first to discover the available contexts. When the same context occurs in more than one file, the server uses the first file in alphabetical order and lists only the ignored file names in `duplicate_kubeconfig_files`.

## Run

```sh
uv run main.py
```

or:

```sh
make run
```

The server uses the MCP `stdio` transport. VS Code can use the included [.vscode/mcp.json](.vscode/mcp.json) configuration.

## Use in VS Code

1. Open this repository as a folder or workspace in VS Code and mark it as trusted.
2. Open the Command Palette with `Ctrl+Shift+P` and run `MCP: List Servers`.
3. Select `kubernetes-readonly` and start it if it is not already running. VS Code executes `uv run main.py` from this workspace using [.vscode/mcp.json](.vscode/mcp.json).
4. In Copilot Chat, select the `kubernetes-readonly` server in the tools picker. Ask `list_clusters` first, then use the returned context name as the `cluster` argument in subsequent requests.

For example: `Use list_clusters and then show the health of the context <context-name>.` The server runs over `stdio`, so do not start it manually with `make run` while VS Code is managing the server.

## Available diagnostics

- `list_clusters`, `get_cluster_health`, `list_nodes`, `list_namespaces`
- `list_pods`, `describe_pod`, `get_pod_logs`
- `list_deployments`, `list_statefulsets`, `list_events`
- `list_storage`, `list_rbac`, `get_configmap`, `list_secrets`
- `list_crds`, `list_api_resources`, `list_resources`, `get_resource`

Use `list_crds` to discover installed CRDs, their groups, kinds, scopes, and served versions. Use `list_api_resources` to discover any built-in or custom Kubernetes API resource available to the selected context. Then use `list_resources` or `get_resource` with the returned `api_version` and `kind` to read full resource objects, including CRDs and Secret data when Kubernetes RBAC allows it. `list_resources` supports `namespace`, `label_selector`, `field_selector`, `limit`, and `continue_token` for large result sets; omitting `namespace` lists namespaced resources across the whole cluster.

Pod logs are limited to 2,000 lines per request. They may contain sensitive application data. ConfigMap values are returned for diagnosis. `list_secrets` returns only Secret metadata and key names, while `get_resource` can return the full Secret object for agents that need unrestricted read access and have RBAC permission.

## Read-only boundary

Every tool is marked with MCP's read-only annotation and directly invokes only Kubernetes discovery, `read_*`, `list_*`, log read or dynamic-client `GET` calls. The server exposes no shell commands, watch streams, pod execution, port forwarding, or mutating Kubernetes APIs. The annotation is advisory, so actual protection must also come from read-only Kubernetes RBAC assigned to the credentials in each kubeconfig.