#
# Zabbix MCP Server
# Copyright (C) 2026 initMAX s.r.o.
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

FROM registry.access.redhat.com/ubi9/python-311:latest AS builder

WORKDIR /build
COPY . .
# Install build dependencies
USER 0
RUN microdnf update -y && microdnf install -y \
    gcc \
    make \
    git \
    libffi-devel \
    cairo-devel \
    pango-devel \
    gdk-pixbuf2-devel \
    shared-mime-info \
    && microdnf clean all

# Create venv and install dependencies
RUN python -m venv /opt/zabbix-mcp/venv \
    && /opt/zabbix-mcp/venv/bin/pip install --upgrade pip \
    && /opt/zabbix-mcp/venv/bin/pip install --no-cache-dir --quiet ".[reporting]"

FROM registry.access.redhat.com/ubi9/python-311:latest

LABEL maintainer="initMAX s.r.o. <info@initmax.com>"
LABEL org.opencontainers.image.title="Zabbix MCP Server"
LABEL org.opencontainers.image.description="MCP server for the complete Zabbix API"
LABEL org.opencontainers.image.source="https://github.com/initMAX/zabbix-mcp-server"
LABEL org.opencontainers.image.url="https://github.com/initMAX/zabbix-mcp-server"
LABEL org.opencontainers.image.documentation="https://github.com/initMAX/zabbix-mcp-server/blob/main/README.md"
LABEL org.opencontainers.image.vendor="initMAX s.r.o."
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
LABEL org.opencontainers.image.version="1.25"


# System libs for weasyprint PDF rendering
USER 0
RUN microdnf update -y && microdnf install -y \
    cairo \
    pango \
    gdk-pixbuf2 \
    libffi \
    shared-mime-info \
    && microdnf clean all

RUN useradd --system --shell /usr/sbin/nologin --home-dir /opt/zabbix-mcp zabbix-mcp \
    && mkdir -p /var/log/zabbix-mcp /etc/zabbix-mcp \
    && mkdir -p /etc/zabbix-mcp/assets /etc/zabbix-mcp/tls /etc/zabbix-mcp/templates \
    && chown -R zabbix-mcp:0 /var/log/zabbix-mcp /etc/zabbix-mcp /opt/zabbix-mcp \
    && chmod -R g=u /var/log/zabbix-mcp /etc/zabbix-mcp /opt/zabbix-mcp \
    && chmod 750 /etc/zabbix-mcp/tls /etc/zabbix-mcp/templates

COPY --from=builder /opt/zabbix-mcp/venv /opt/zabbix-mcp/venv

ENV PATH="/opt/zabbix-mcp/venv/bin:$PATH"


# OpenShift: allow random UID by keeping root, but files are group writable
USER 0
EXPOSE 8080
EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; import sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8080/health').status == 200 else sys.exit(1)"]

ENTRYPOINT ["/opt/zabbix-mcp/venv/bin/zabbix-mcp-server"]
CMD ["--config", "/etc/zabbix-mcp/config.toml"]
