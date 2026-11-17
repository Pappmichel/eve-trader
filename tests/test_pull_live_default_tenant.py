"""The live-data pull script must never copy secrets or private text."""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "deploy" / "pull_live_default_tenant.py"


@pytest.fixture(scope="module")
def pull():
    spec = importlib.util.spec_from_file_location("pull_live_default_tenant", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # importing must not run the pull
    return module


@pytest.mark.parametrize("table", [
    "tenant_tokens", "access_requests", "access_allowlist",
    "character_session_revocations", "alert_destinations",
    "mail_messages", "mail_recipients", "mail_character_headers", "mail_labels", "mail_lists",
    "character_notifications", "character_notification_reads",
])
def test_excluded_tables(pull, table):
    assert pull.excluded(table)


@pytest.mark.parametrize("table", [
    "portfolio_snapshots", "character_skills", "char_mail_archive_settings", "sde_types",
])
def test_other_tables_are_copied(pull, table):
    assert not pull.excluded(table)
