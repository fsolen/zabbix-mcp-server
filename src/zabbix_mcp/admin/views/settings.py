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
RESTART_REQUIRED = {"host", "port", "transport", "tls_cert_file", "tls_key_file", "log_file"}

# List fields — split comma-separated into TOML arrays
LIST_KEYS = {"cors_origins", "allowed_hosts", "allowed_import_dirs", "tools", "disabled_tools"}

# Boolean fields — checkbox present = True, absent = False
BOOL_KEYS = {"compact_output", "enabled"}

# Map UI section names to actual config.toml section + allowed keys
SECTION_CONFIG = {
    "server": {
        "toml_section": "server",
        "allowed_keys": {"host", "port", "transport", "log_level", "log_file", "compact_output", "response_max_chars", "public_url"},
        "min_role": "admin",
    },
    "tls_access": {
        "toml_section": "server",
        "allowed_keys": {"tls_cert_file", "tls_key_file", "cors_origins", "allowed_hosts", "allowed_import_dirs", "rate_limit"},
        "min_role": "admin",
    },
    "tools": {
        "toml_section": "server",
        "allowed_keys": {"tools", "disabled_tools"},
        "min_role": "admin",
    },
    "reporting": {
        "toml_section": "server",
        "allowed_keys": {"report_company", "report_subtitle", "report_logo"},
        "min_role": "operator",
    },
    "admin": {
        "toml_section": "admin",
        "allowed_keys": {"enabled", "port"},
        "min_role": "admin",
    },
    # [admin.ai] - optional sub-table driving the "Generate with AI"
    # button on the report template editor. Leaving api_key blank in
    # the form is treated as "keep existing" so the operator does not
    # have to paste their key every save.
    "admin_ai": {
        "toml_section": "admin.ai",
        "allowed_keys": {"enabled", "provider", "api_key", "model", "api_base", "timeout", "max_tokens"},
        "min_role": "admin",
    },
}

# Keys that must not be cleared when the submitted value is empty.
# The settings UI sends "" for api_key when the operator does not want
# to rotate the stored secret; treat that as "keep current value".
SECRET_KEEP_EMPTY = {"api_key"}


async def settings_view(request: Request) -> Response:
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    # Read current config — keep server and admin sections separate
    settings: dict = {}
    has_legacy_token = False

    if TOMLKIT_AVAILABLE:
        try:
            doc = load_config_document(admin_app.config_path)
            server_cfg = dict(doc.get("server", {}))
            admin_cfg = dict(doc.get("admin", {}))

            # Detect legacy auth_token
            if server_cfg.get("auth_token"):
                has_legacy_token = True

            # Remove sensitive values
            server_cfg.pop("auth_token", None)
            # Remove users sub-table from admin display
            admin_cfg.pop("users", None)

            # Merge server fields directly
            settings.update(server_cfg)

            # Admin fields — prefix to avoid collision (both have "port")
            settings["admin_enabled"] = admin_cfg.get("enabled", False)
            settings["admin_port"] = admin_cfg.get("port", 9090)

            # [admin.ai] sub-table. We expose the provider, model,
            # and enabled flag verbatim, but never the raw api_key -
            # instead we just report whether one is configured so the
            # UI can display "Key configured" without leaking it.
            ai_cfg = dict(admin_cfg.get("ai", {})) if isinstance(admin_cfg.get("ai"), dict) else {}
            # Default True matches AdminAIConfig.enabled so legacy
            # configs without the flag continue to show the feature as
            # enabled in the UI.
            settings["ai_enabled"] = bool(ai_cfg.get("enabled", True))
            settings["ai_provider"] = ai_cfg.get("provider", "")
            settings["ai_model"] = ai_cfg.get("model", "")
            settings["ai_api_base"] = ai_cfg.get("api_base", "")
            settings["ai_api_key_configured"] = bool(ai_cfg.get("api_key"))
            settings["ai_timeout"] = int(ai_cfg.get("timeout") or 180)
            settings["ai_max_tokens"] = int(ai_cfg.get("max_tokens") or 8000)
        except Exception as e:
            logger.error("Failed to read config: %s", e)

    return admin_app.render("settings.html", request, {
        "active": "settings",
        "settings": settings,
        "restart_required_fields": RESTART_REQUIRED,
        "has_legacy_token": has_legacy_token,
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

    # Field-level validation: catch bad input before it lands in
    # config.toml and bricks the next server start.
    if "public_url" in allowed_keys and "public_url" in form:
        public_url_raw = str(form.get("public_url", "") or "").strip()
        if public_url_raw:
            try:
                from zabbix_mcp.config import _validate_public_url
                # Pass current tls_cert_file so https/http requirement
                # is enforced consistently with config.py validation.
                tls = getattr(admin_app.config.server, "tls_cert_file", None)
                _validate_public_url(public_url_raw, tls)
            except Exception as exc:
                return admin_app.flash_redirect(
                    "/settings", f"Public URL is invalid: {exc}", "danger"
                )

    try:
        doc = load_config_document(admin_app.config_path)
        import tomlkit

        # Support dotted section names (e.g. "admin.ai" for nested
        # TOML sub-tables) by walking the path and creating missing
        # tables as we go.
        parts = config_section_name.split(".")
        config_section = doc
        for i, part in enumerate(parts):
            if part not in config_section:
                config_section.add(part, tomlkit.table())
            config_section = config_section[part]

        needs_restart = False

        for key in allowed_keys:
            old_value = config_section.get(key)

            if key in BOOL_KEYS:
                new_value = key in form
                config_section[key] = new_value
            elif key in LIST_KEYS:
                raw = str(form.get(key, "")).strip()
                if raw:
                    new_value = [s.strip() for s in raw.split(",") if s.strip()]
                    config_section[key] = new_value
                else:
                    new_value = None
                    if key in config_section:
                        del config_section[key]
            elif key in form:
                value = str(form.get(key, "")).strip()
                if value == "":
                    # Secrets like api_key: blank form value means
                    # "don't touch the stored value" so the operator
                    # does not have to re-paste the key on every save.
                    if key in SECRET_KEEP_EMPTY:
                        continue
                    new_value = None
                    if key in config_section:
                        del config_section[key]
                    continue
                if value.isdigit():
                    value = int(value)
                new_value = value
                config_section[key] = value
            else:
                continue

            # Flag restart if any field actually changed
            old_cmp = str(old_value) if old_value is not None else ""
            new_cmp = str(new_value) if new_value is not None else ""
            if old_cmp != new_cmp:
                needs_restart = True

        save_config_document(admin_app.config_path, doc)
        logger.info("Settings [%s] updated by %s", section, session.user)
        from zabbix_mcp.admin.audit_writer import write_audit
        client_ip = request.client.host if request.client else ""
        write_audit("settings_update", user=session.user, target_type="settings", target_id=section, ip=client_ip)

        if needs_restart:
            admin_app.restart_needed = True

        msg = "Settings saved."
        if needs_restart:
            msg += " Restart required to apply changes."
        return admin_app.flash_redirect("/settings", msg)

    except Exception as e:
        logger.error("Failed to update settings: %s", e)
        return admin_app.flash_redirect("/settings", f"Failed to save settings: {e}", "danger")
