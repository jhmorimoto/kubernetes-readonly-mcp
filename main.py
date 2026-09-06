import logging
import sys

from k8s_readonly_mcp.cluster import get_kubeconfig_directory
from k8s_readonly_mcp.server import mcp


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("k8s_readonly_mcp").info(
        "Kubeconfig directory in use: %s", get_kubeconfig_directory()
    )
    mcp.run(transport="stdio")