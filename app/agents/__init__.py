"""Eight fixed local domain agents for stage four of the demo."""

from app.agents.runner import AgentRunResult, FixedAgentRunner
from app.agents.mcp_gateway import FastMCPGateway
from app.agents.specs import AGENT_SPECS, AgentSpec

__all__ = ["AGENT_SPECS", "AgentRunResult", "AgentSpec", "FastMCPGateway", "FixedAgentRunner"]
