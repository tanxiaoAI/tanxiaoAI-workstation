#!/usr/bin/env python3
"""进程级统一 HTTPS 证书信任。

导入本模块即生效，不需要调用任何函数。

python.org 版 Python 在 macOS / Windows 上可能没有可用根证书，`urllib` 一发
HTTPS 就报 `SSL: CERTIFICATE_VERIFY_FAILED`。这里给默认 SSLContext 追加可信
根证书（系统默认 + certifi + 常见系统证书包），所有联网执行器共用一份信任链，
不再依赖用户在机器上临时改环境变量或换证书路径。
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path

_ORIGINAL_CREATE_DEFAULT_CONTEXT = ssl.create_default_context

_CA_ENV_KEYS = ("MEILING_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")

_SYSTEM_CA_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-bundle.crt",
    "/usr/local/etc/openssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
)


def _candidate_bundles() -> list[str]:
    bundles: list[str] = []
    for key in _CA_ENV_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            bundles.append(value)
    try:
        import certifi  # 装了就用官方 CA 包

        bundles.append(certifi.where())
    except Exception:
        pass
    bundles.extend(_SYSTEM_CA_BUNDLES)
    return bundles


def _trusted_default_context(*args, **kwargs):
    context = _ORIGINAL_CREATE_DEFAULT_CONTEXT(*args, **kwargs)
    for bundle in _candidate_bundles():
        try:
            if bundle and Path(bundle).is_file():
                context.load_verify_locations(cafile=bundle)
        except Exception:
            continue
    return context


def build_context() -> ssl.SSLContext:
    """按当前机器可用的证书包构建一个已追加信任的 SSLContext。"""
    return _trusted_default_context()


ssl.create_default_context = _trusted_default_context  # type: ignore[assignment]
ssl._create_default_https_context = _trusted_default_context  # type: ignore[attr-defined]
