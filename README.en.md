# JudgeFlow

[简体中文](README.md) | [English](README.en.md)

JudgeFlow is a multi-agent system for investigation, adjudication, appeals, and policy evolution in high-risk content governance. It separates evidence collection, adversarial review, independent judgment, and rule improvement into verifiable and traceable workflows with explicit role boundaries.

This repository contains the first complete runnable version of JudgeFlow. It proves the core workflows with a fixed case and bounded infrastructure and has been validated for a 5–8 minute live walkthrough. These are the boundaries of the current version, not the limits of the project's future evolution.

## Core workflows

Case adjudication:

```text
Investigation → Risk argument and counter-review → Independent judgment
```

Policy evolution:

```text
Appeal overturn → Root-cause attribution → Policy draft → Deterministic replay → Human approval
```

The current version does not execute real enforcement actions, change user rights, or publish or replace production policies. It also does not attempt to provide a general moderation platform, production authorization center, RAG system, message queue, or additional microservices. Formal outputs must pass structured Schema and backend validation; agents cannot write the database or advance business state directly.

The example `judgeflow/judgeflow` database credentials are only for an isolated database bound to the local loopback interface. They must never be used on public, shared, or production systems. Model API keys and actual database connections are supplied only through private configuration that is not committed.

## Current capabilities

- 8 domain agents, 3 coordination agents, and 1 human role;
- AgentTeams / Matrix organization, task routing, and on-demand agent activation;
- 5 minimal MCP tools and 4 reusable Skills;
- 8 PostgreSQL business tables with immutable artifacts, policy versions, and audit events;
- deterministic Python policy execution and historical replay—LLMs do not calculate metrics;
- a single-page run monitor, case center, policy center, approval queue, and replay detail view;
- distinct semantics for success, execution failure, not started, and no business data;
- Human APPROVE records approval of a candidate only and does not publish it to production.

## Documentation and verification

- [Operations guide (Chinese)](项目启动手册.md): startup, health checks, model configuration, rebuild, recovery, and troubleshooting;
- [Product UI handoff (Chinese)](docs/JudgeFlow-产品化页面开发交接.md): current UI, read projections, data mapping, and real-data gaps;
- [Minimal technical design (Chinese)](docs/JudgeFlow-比赛Demo技术设计.md): architecture, agent boundaries, state machines, and data design;
- [Stage 6 acceptance report (Chinese)](验收成果/阶段-6-最终Demo/验收报告.md): complete workflows, failure/idempotency behavior, database evidence, and walkthrough timing;
- [Stage 5 AgentTeams report (Chinese)](验收成果/阶段-5-AgentTeams/验收报告.md): dynamic AgentTeams, Matrix, and MCP evidence;
- [Product concept](frontend/product-concept.html): information architecture and interaction reference; its static mock data is not used at runtime.

Read the operations guide before running the current version. Do not reinstall AgentTeams, recreate the database, or delete Docker volumes based on historical stage reports.

## Repository layout

```text
app/
├── core/                # JudgeFlowKernel: artifact validation and state progression
├── demo/                # stdlib HTTP service, fixed workflow, and read projections
├── agents/              # deterministic runners for the eight domain agents
├── agentteams/          # AgentTeams message contracts and HTTP MCP transport
├── mcp/                 # FastMCP server exposing five tools
├── replay/              # deterministic policy execution and replay
├── models/              # Pydantic schemas and eight business-table models
└── api/                 # empty placeholder; current HTTP entry is app.demo.server
agentteams/
├── resources/           # Manager, Team, Worker, and Human resources
└── packages/            # minimal runtime packages for agents
skills/                  # reusable agent workflows
frontend/                # current single-page UI and product concept
fixtures/                # fixed case, appeal, evidence, policy, and replay data
tests/                   # schema, contract, and workflow tests
migrations/              # database migration created for this project
docs/                    # current technical design and development handoff
验收成果/                # historical acceptance evidence by stage
```

A directory containing only `.gitkeep` is a scaffold placeholder, not an implemented component.

## Running the UI

See the [operations guide](项目启动手册.md) for startup, health checks, and private database configuration. After starting the page process, open `http://127.0.0.1:8086/`.

The current UI is served by `frontend/index.html`, `frontend/app.css`, `frontend/refinement.css`, and `frontend/app.js`. It consumes real `/api/demo/*` projections for monitoring, cases, policies, approvals, and replays. The browser never connects directly to the database, agents, Matrix, or MCP.

The fixed walkthrough covers:

1. the case and its locked policy snapshot;
2. investigation, risk argument, counter-review, and independent judgment;
3. the frozen original decision, new evidence, and appeal overturn;
4. `POLICY_GAP` versus `EVIDENCE_GAP → CLOSED`;
5. the candidate-policy diff and deterministic replay;
6. Human APPROVE while the candidate remains unpublished and BASELINE remains unchanged;
7. WorkOrders, Artifacts, `run_id`, `trace_id`, and explicit TIMEOUT failure evidence.

## Tests

```bash
UV_CACHE_DIR=/tmp/judgeflow-uv-cache uv run pytest -q
```

Without `TEST_DATABASE_URL`, the online PostgreSQL migration and persistence tests are explicitly skipped. See the [operations guide](项目启动手册.md) for the full acceptance command and current baseline.

## License

No open-source license is granted for this repository. All rights are reserved by default.
