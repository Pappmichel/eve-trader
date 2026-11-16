"""Static checks of the server templates (systemd, nginx, setup.sh)."""
import re
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "deploy"
_read = lambda n: (D / n).read_text(encoding="utf-8")


def test_service_template_hardening_directives():
    t = _read("eve-trader.service.template")
    for d in ("UMask=0027", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=full",
              "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
              "RestrictSUIDSGID=yes", "LockPersonality=yes"):
        assert re.search(rf"^{d}$", t, re.M), d
    assert not re.search(r"^(ProtectHome|MemoryMax|CPUQuota)=", t, re.M)


def test_setup_service_user_and_node():
    s = _read("setup.sh")
    assert 'APP_USER="${APP_USER:-$(whoami)}"' not in s
    assert "useradd --system --no-create-home" in s
    assert "sudo useradd" in s and "usermod" not in s and "-G sudo" not in s
    assert "setup_22.x" in s and "setup_20.x" not in s
    assert "nginx-eve-trader-ssl.conf.template" in s


def test_nginx_templates():
    for name in ("nginx-eve-trader.conf.template", "nginx-eve-trader-ssl.conf.template"):
        t = _read(name)
        for needle in ("server_tokens off;", "client_max_body_size 1m;", "limit_req_status 429;",
                       "log_format eve_trader_timing", "proxy_set_header X-Request-ID $request_id;",
                       "rate=10r/s", "rate=6r/m", "Content-Security-Policy"):
            assert needle in t, (name, needle)
        assert "log_format main" not in t
    ssl = _read("nginx-eve-trader-ssl.conf.template")
    assert 'Strict-Transport-Security "max-age=15768000" always' in ssl
    assert "includeSubDomains" not in ssl
    assert "return 301 https://" in ssl
    assert "add_header Strict-Transport-Security" not in _read("nginx-eve-trader.conf.template")
