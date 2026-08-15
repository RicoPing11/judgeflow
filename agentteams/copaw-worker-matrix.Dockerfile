FROM higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-copaw-worker:v1.2.0

ENV LLM_MAX_CONCURRENT=1

# AgentTeams installs each CoPaw profile below /root/.copaw-worker, while the
# upstream image also changes HOME to its transient agentteams-fs directory.
# Pin CoPaw back to the install directory so config.json and Matrix plugins
# written by the official sync process are loaded by the runtime.
RUN sed -i '/CONSOLE_PORT="${AGENTTEAMS_CONSOLE_PORT:-}"/a\export COPAW_WORKING_DIR="${INSTALL_DIR}/${WORKER_NAME}/.copaw"\nexport COPAW_SECRET_DIR="${INSTALL_DIR}/${WORKER_NAME}/.copaw.secret"' /opt/agentteams/scripts/copaw-worker-entrypoint.sh
