FROM higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-manager-copaw:v1.2.0

WORKDIR /srv/judgeflow

COPY app ./app
COPY fixtures ./fixtures

RUN /opt/copaw-venv/bin/pip install --no-cache-dir "psycopg[binary]>=3.2,<4"

ENV PYTHONUNBUFFERED=1 \
    JUDGEFLOW_TRUST_NATIVE_HIGRESS=1

ENTRYPOINT []
CMD ["/opt/copaw-venv/bin/python", "-m", "app.agentteams.mcp_http"]
