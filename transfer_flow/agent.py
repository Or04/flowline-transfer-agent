"""Tool-calling agent with deterministic fake model and authoritative report validation."""
import asyncio
import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

class AgentError(RuntimeError): pass

@dataclass
class ToolCall:
    name: str
    arguments: dict

class ScriptedLLM:
    """Goal-directed deterministic planner. It never interprets transfer memo text."""
    def next(self, goal: str, history: list[dict]) -> ToolCall | dict:
        calls = [x for x in history if x["type"] == "tool"]
        if not calls:
            path = goal.split("Process ", 1)[-1].split(" and ", 1)[0].strip(" '`\".")
            return ToolCall("submit_batch", {"filePath": path})
        if calls[-1].get("error"):
            raise AgentError("Tool failure: " + calls[-1]["error"])
        if len(calls) == 1:
            return ToolCall("get_batch_status", {"batchId": calls[0]["result"]["batchId"]})
        if len(calls) == 2:
            return ToolCall("get_ledger_summary", {})
        status = calls[1]["result"]
        summary = calls[2]["result"]
        return build_report(status, summary)

def build_report(status, summary):
    results = status.get("results")
    if status["status"] != "DONE" or not isinstance(results, list):
        raise AgentError("Batch did not complete")
    return {"batchId": status["batchId"], "status": status["status"],
            "accepted": [r["reference"] for r in results if r["status"] == "ACCEPTED"],
            "rejected": [{"index": r["index"], "reference": r["reference"], "reason": r["code"]} for r in results if r["status"] == "REJECTED"],
            "duplicates": [r["reference"] for r in results if r["status"] == "DUPLICATE"],
            "openingTotal": summary["openingTotal"], "currentTotal": summary["currentTotal"],
            "moneyPreserved": summary["preserved"]}

def validate_report(report: Any, authoritative_status: dict, authoritative_summary: dict):
    if not isinstance(report, dict):
        raise AgentError("Report must be object")
    required = {"batchId": str, "status": str, "accepted": list, "rejected": list,
                "duplicates": list, "openingTotal": str, "currentTotal": str, "moneyPreserved": bool}
    if set(report) != set(required) or any(type(report[k]) is not t for k, t in required.items()):
        raise AgentError("Report schema validation failed")
    for item in report["rejected"]:
        if not isinstance(item, dict) or set(item) != {"index", "reference", "reason"} or type(item["index"]) is not int or not isinstance(item["reason"], str):
            raise AgentError("Invalid rejected row schema")
    if report != build_report(authoritative_status, authoritative_summary):
        raise AgentError("Report contradicts authoritative ledger results")
    return report

class AgentHarness:
    def __init__(self, tools, llm=None, max_steps=6):
        self.tools = tools
        self.llm = llm or ScriptedLLM()
        self.max_steps = max_steps
        self.trace = []

    async def run(self, goal):
        history = []
        for step in range(self.max_steps):
            decision = self.llm.next(goal, history)
            if isinstance(decision, dict):
                self.trace.append({"step": step + 1, "type": "report", "validated": False})
                if len(history) < 3:
                    raise AgentError("Insufficient authoritative evidence")
                report = validate_report(decision, history[1]["result"], history[2]["result"])
                self.trace[-1]["validated"] = True
                return report
            if not isinstance(decision, ToolCall) or decision.name not in {"submit_batch", "get_batch_status", "get_ledger_summary"}:
                raise AgentError("Unapproved tool call")
            event = {"step": step + 1, "type": "tool", "name": decision.name, "arguments": decision.arguments}
            try:
                result = await self.tools.call(decision.name, decision.arguments)
                event["result"] = result
                history.append(event)
            except Exception as exc:
                event["error"] = str(exc)
                history.append(event)
            self.trace.append(event)
        raise AgentError("Step budget exhausted")

class LocalTools:
    """In-process adapter used by tests; same tool contract as MCP."""
    def __init__(self, client, allowed_dir):
        self.client = client
        self.allowed_dir = Path(allowed_dir).resolve()
    async def call(self, name, arguments):
        if name == "submit_batch":
            path = Path(arguments["filePath"]).resolve()
            if not path.is_relative_to(self.allowed_dir):
                raise ValueError("File outside allowlist")
            return self.client.submit(path)
        if name == "get_batch_status":
            return self.client.status(arguments["batchId"])
        if name == "get_ledger_summary":
            return self.client.summary()
        raise ValueError("Unknown tool")

class MCPTools:
    """Real MCP stdio transport, used by CLI agent."""
    async def __aenter__(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=["-m", "transfer_flow.mcp_server"], env={**os.environ})
        self.ctx = stdio_client(params)
        streams = await self.ctx.__aenter__()
        self.session = ClientSession(*streams)
        await self.session.__aenter__()
        await self.session.initialize()
        return self
    async def __aexit__(self, *args):
        await self.session.__aexit__(*args)
        await self.ctx.__aexit__(*args)
    async def call(self, name, arguments):
        response = await self.session.call_tool(name, arguments)
        if response.isError:
            raise AgentError(str(response.content))
        if response.structuredContent is not None:
            return response.structuredContent
        return json.loads(response.content[0].text)

async def main():
    import argparse
    from .client import TransferClient
    from .ingestion import parse_file
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", default="transfers.json")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--trace", default="agent-trace.json")
    args = parser.parse_args()
    file = Path(args.file).resolve()
    os.environ["TRANSFER_API_URL"] = args.url
    os.environ["TRANSFER_ALLOWED_DIR"] = str(file.parent)
    client = TransferClient(args.url)
    client.reset(parse_file(file)["accounts"])
    agent = None
    try:
        async with MCPTools() as tools:
            agent = AgentHarness(tools)
            report = await agent.run(f"Process {file} and give me a reconciliation report")
            print(json.dumps(report, indent=2))
    finally:
        if agent:
            Path(args.trace).write_text(json.dumps(agent.trace, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
