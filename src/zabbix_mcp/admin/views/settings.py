#
# Zabbix MCP Server
# Copyright (C) 2026 initMAX s.r.o.
#

"""Settings view — display and edit all config.toml sections."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from zabbix_mcp.admin.config_writer import (
    load_config_document,
    save_config_document,
    TOMLKIT_AVAILABLE,
)

logger = logging.getLogger("zabbix_mcp.admin")

# Settings that require a server restart to take effect
RESTART_REQUIRED = {"host", "port", "transport", "tls_cert_file", "tls_key_file"}

# Map UI section names to actual config.toml section + allowed keys
SECTION_CONFIG = {
    "server": {
        "toml_section": "server",
        "allowed_keys": {"host", "port", "transport", "log_level", "compact_output"},
        "min_role": "operator",
    },
    "security": {
        "toml_section": "server",
        "allowed_keys": {"rate_limit"},
        "min_role": "operator",
    },
    "reporting": {
        "toml_section": "server",
        "allowed_keys": {"report_company", "report_subtitle", "report_logo"},
        "min_role": "operator",
    },
    "admin": {
        "toml_section": "admin",
        "allowed_keys": {"port", "enabled"},
        "min_role": "admin",  # only admin can modify admin section
    },
}


async def settings_view(request: Request) -> Response:
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    # Read current config — flatten sections so template can use settings.host etc.
    settings = {}
    if TOMLKIT_AVAILABLE:
        try:
            doc = load_config_document(admin_app.config_path)
            server_cfg = dict(doc.get("server", {}))
            admin_cfg = dict(doc.get("admin", {}))
            # Remove sensitive values
            server_cfg.pop("auth_token", None)
            # Remove users sub-table from admin display
            admin_cfg.pop("users", None)
            # Merge all into flat dict
            settings.update(server_cfg)
            settings.update(admin_cfg)
        except Exception as e:
            logger.error("Failed to read config: %s", e)

    return admin_app.render("settings.html", request, {
        "active": "settings",
        "settings": settings,
        "restart_required_fields": RESTART_REQUIRED,
        "can_edit": session.role in ("admin", "operator"),
    })


async def settings_update(request: Request) -> Response:
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session or session.role not in ("admin", "operator"):
        return RedirectResponse("/settings", status_code=303)

    section = request.path_params["section"]
    section_cfg = SECTION_CONFIG.get(section)
    if not section_cfg:
        return RedirectResponse("/settings", status_code=303)

    # Check minimum role for this section
    if section_cfg["min_role"] == "admin" and session.role != "admin":
        logger.warning("User '%s' (role=%s) denied access to settings/%s", session.user, session.role, section)
        return RedirectResponse("/settings", status_code=303)

    config_section_name = section_cfg["toml_section"]
    allowed_keys = section_cfg["allowed_keys"]

    form = await request.form()

    try:
        doc = load_config_document(admin_app.config_path)
        config_section = doc.get(config_section_name, {})

        needs_restart = False
        for key, value in form.items():
            if key.startswith("_"):
                continue

            # SECURITY: reject keys not in allowlist
            if key not in allowed_keys:
                logger.warning("Rejected setting key '%s' in section '%s' (not in allowlist)", key, section)
                continue

            # Type conversion
            if value == "true":
                value = True
            elif value == "false":
                value = False
            elif value.isdigit():
                value = int(value)

            if value == "" and key in config_section and config_section[key] is None:
                continue

            config_section[key] = value

            if key in RESTART_REQUIRED:
                needs_restart = True

        save_config_document(admin_app.config_path, doc)
        logger.info("Settings [%s] updated by %s", section, session.user)

        if not needs_restart:
            from zabbix_mcp.admin.config_writer import signal_reload
            signal_reload()

    except Exception as e:
        logger.error("Failed to update settings: %s", e)

    return RedirectResponse("/settings", status_code=303)
