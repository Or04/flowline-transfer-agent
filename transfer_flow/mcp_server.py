"""Official MCP stdio server. Tool writes require an explicit allowlisted path."""
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from .client import TransferClient

mcp = FastMCP("transfer-flow")

def client():
    return TransferClient(os.environ.get("TRANSFER_API_URL", "http://127.0.0.1:8765"))

@mcp.tool()
def submit_batch(filePath: str) -> dict:
    """Submit a local transfer file. Only files inside TRANSFER_ALLOWED_DIR are permitted."""
    allowed = Path(os.environ.get("TRANSFER_ALLOWED_DIR", os.getcwd())).resolve()
    candidate = Path(filePath).resolve()
    if not candidate.is_relative_to(allowed) or candidate.suffix.lower() not in (".json", ".xlsx") or not candidate.is_file():
        raise ValueError("File outside allowlisted directory or invalid format")
    return client().submit(candidate)

@mcp.tool()
def get_batch_status(batchId: str) -> dict:
    """Read the authoritative batch status and row outcomes."""
    return client().status(batchId)

@mcp.tool()
def get_ledger_summary() -> dict:
    """Read the authoritative conservation-of-money summary."""
    return client().summary()

if __name__ == "__main__":
    mcp.run(transport="stdio")
