"""Run the local stdio MCP server against the configured JudgeFlow database."""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .server import JudgeFlowMCPService, create_mcp_server


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    consumer = os.environ["JUDGEFLOW_CONSUMER"]
    with Session(create_engine(database_url)) as session:
        create_mcp_server(JudgeFlowMCPService(session), consumer).run(transport="stdio")


if __name__ == "__main__":
    main()
