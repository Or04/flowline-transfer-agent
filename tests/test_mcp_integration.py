"""Real stdio MCP transport smoke test (requires installed project dependencies)."""
import asyncio
import json
from pathlib import Path

import pytest

from transfer_flow.agent import AgentHarness, MCPTools
from transfer_flow.ingestion import parse_file
from transfer_flow.mock import MockServer
from transfer_flow.client import TransferClient


@pytest.mark.skipif(__import__('importlib').util.find_spec('mcp') is None, reason='MCP SDK not installed in offline test environment')
def test_real_mcp_stdio_end_to_end(monkeypatch, tmp_path):
    """A real MCP subprocess processes the supplied file and reconciles every row."""
    source = Path(__file__).resolve().parents[1] / 'transfers.json'
    with MockServer(tmp_path / 'ledger.sqlite') as mock:
        client = TransferClient(mock.url)
        client.reset(parse_file(source)['accounts'])
        monkeypatch.setenv('TRANSFER_API_URL', mock.url)
        monkeypatch.setenv('TRANSFER_ALLOWED_DIR', str(source.parent))

        async def exercise():
            async with MCPTools() as tools:
                return await AgentHarness(tools).run(f'Process {source} and give me a reconciliation report')

        report = asyncio.run(exercise())
        assert len(report['accepted']) == 4
        assert len(report['rejected']) == 7
        assert len(report['duplicates']) == 1
        assert report['moneyPreserved'] is True
        assert mock.state.ledger.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 8
