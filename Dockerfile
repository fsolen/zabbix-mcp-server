FROM registry.access.redhat.com/ubi9/python-312 AS builder

USER root
WORKDIR /build

RUN dnf install -y \
        gcc \
        python3-devel \
        libffi-devel \
    && dnf clean all

COPY . .

RUN python3 -m venv /opt/zabbix-mcp/venv \
    && /opt/zabbix-mcp/venv/bin/pip install --upgrade pip \
    && /opt/zabbix-mcp/venv/bin/pip install --no-cache-dir ".[reporting]"

FROM registry.access.redhat.com/ubi9/python-312

LABEL maintainer="initMAX s.r.o. <info@initmax.com>"
LABEL org.opencontainers.image.title="Zabbix MCP Server"
LABEL org.opencontainers.image.description="MCP server for the complete Zabbix API"
LABEL org.opencontainers.image.source="https://github.com/initMAX/zabbix-mcp-server"
LABEL org.opencontainers.image.url="https://github.com/initMAX/zabbix-mcp-server"
LABEL org.opencontainers.image.documentation="https://github.com/initMAX/zabbix-mcp-server/blob/main/README.md"
LABEL org.opencontainers.image.vendor="initMAX s.r.o."
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
LABEL org.opencontainers.image.version="1.25"

USER root

RUN dnf install -y \
        cairo \
        pango \
        gdk-pixbuf2 \
        libffi \
        shared-mime-info \
    && dnf clean all

RUN useradd --system --home-dir /opt/zabbix-mcp --shell /sbin/nologin zabbix-mcp \
    && mkdir -p /var/log/zabbix-mcp /etc/zabbix-mcp \
    && mkdir -p /etc/zabbix-mcp/assets /etc/zabbix-mcp/tls /etc/zabbix-mcp/templates \
    && chown -R zabbix-mcp:zabbix-mcp /var/log/zabbix-mcp /etc/zabbix-mcp \
    && chmod 750 /etc/zabbix-mcp/tls /etc/zabbix-mcp/templates

COPY --from=builder /opt/zabbix-mcp/venv /opt/zabbix-mcp/venv

ENV PATH="/opt/zabbix-mcp/venv/bin:$PATH"

USER zabbix-mcp

EXPOSE 8080
EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

ENTRYPOINT ["/opt/zabbix-mcp/venv/bin/zabbix-mcp-server"]
CMD ["--config", "/etc/zabbix-mcp/config.toml"]
