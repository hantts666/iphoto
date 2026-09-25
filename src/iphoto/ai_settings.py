"""Non-secret configuration and endpoint-bound Windows credentials."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QStandardPaths


PROVIDERS = [
    {
        "id": "qianwen_token_plan",
        "name": "千问 AI 平台 · Token Plan 个人版",
        "base_url": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
    },
    {
        "id": "qianwen",
        "name": "千问 AI 平台 · 按量计费",
        "base_url": "https://maas.qianwenaiapi.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
    },
    {
        "id": "qwen",
        "name": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6-terra",
    },
    {"id": "custom", "name": "自定义 · OpenAI 兼容", "base_url": "", "model": ""},
]


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("接口地址格式不正确") from None
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("请输入完整 API 地址，不要在地址中填写 Key、查询参数或密码")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("云端接口必须使用 HTTPS；本机服务可以使用 HTTP")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else 80
    netloc = host + (f":{port}" if port and port != default_port else "")
    return urlunsplit((parts.scheme, netloc, path, "", ""))


@dataclass(frozen=True)
class AISettings:
    provider: str = "qianwen_token_plan"
    base_url: str = PROVIDERS[0]["base_url"]
    model: str = PROVIDERS[0]["model"]
    enabled: bool = True
    remember: bool = True

    @classmethod
    def validated(cls, provider, base_url, model, enabled=True, remember=True):
        if provider not in {item["id"] for item in PROVIDERS}:
            raise ValueError("请选择服务商")
        model = model.strip()
        if not model or len(model) > 160 or any(ord(c) < 32 for c in model):
            raise ValueError("请填写有效的模型名称")
        base_url = normalize_url(base_url)
        if provider == "qianwen_token_plan" and base_url not in {
            PROVIDERS[0]["base_url"],
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        }:
            raise ValueError(
                "Token Plan 请使用套餐专属 API 地址，不能填写控制台网页或按量计费地址"
            )
        return cls(provider, base_url, model, bool(enabled), bool(remember))

    @property
    def credential_target(self):
        identity = f"{self.provider}\n{self.base_url}".encode()
        return "iPhoto/AI/" + sha256(identity).hexdigest()


class WindowsCredentialVault:
    """Generic credentials scoped to the signed-in Windows user; no plaintext file."""

    available = sys.platform == "win32"

    def __init__(self):
        if not self.available:
            return

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self.Credential = Credential
        self.dll = ctypes.WinDLL("advapi32", use_last_error=True)
        self.dll.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        self.dll.CredReadW.restype = wintypes.BOOL
        self.dll.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self.dll.CredWriteW.restype = wintypes.BOOL
        self.dll.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.dll.CredDeleteW.restype = wintypes.BOOL
        self.dll.CredFree.argtypes = [ctypes.c_void_p]
        self.dll.CredFree.restype = None

    def get(self, target):
        if not self.available:
            return ""
        pointer = ctypes.POINTER(self.Credential)()
        if not self.dll.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return ""
            raise ValueError("无法读取 Windows 凭据，请重新输入 Key，或选择仅本次使用")
        try:
            value = pointer.contents
            return ctypes.string_at(
                value.CredentialBlob, value.CredentialBlobSize
            ).decode("utf-8")
        finally:
            self.dll.CredFree(pointer)

    def set(self, target, secret):
        if not self.available:
            raise ValueError("此平台暂不支持持久保存 Key，请关闭记住 Key")
        encoded = secret.encode("utf-8")
        if len(encoded) > 2400:
            raise ValueError("Key 过长，无法存入系统凭据库")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        value = self.Credential(
            Type=1,
            TargetName=target,
            CredentialBlobSize=len(encoded),
            CredentialBlob=blob,
            Persist=2,
            UserName="iPhoto",
        )
        if not self.dll.CredWriteW(ctypes.byref(value), 0):
            if ctypes.get_last_error() == 1312:
                raise ValueError(
                    "当前进程无法访问 Windows 登录凭据。请从 start-iphoto.cmd 启动，或关闭记住 Key"
                )
            raise ValueError("Windows 凭据保存失败；可关闭记住 Key 后使用")

    def delete(self, target):
        if (
            self.available
            and not self.dll.CredDeleteW(target, 1, 0)
            and ctypes.get_last_error() != 1168
        ):
            raise ValueError("Windows 凭据删除失败")


class SettingsStore:
    def __init__(self, directory=None, vault=None):
        self.directory = (
            Path(directory)
            if directory is not None
            else Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        )
        self.path = self.directory / "ai-settings.json"
        self.vault = vault if vault is not None else WindowsCredentialVault()
        self.session_keys = {}
        self.load_error = ""

    def load(self):
        if not self.path.exists():
            return AISettings()
        try:
            if self.path.stat().st_size > 8192:
                raise ValueError()
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return AISettings.validated(**data)
        except (ValueError, TypeError, OSError):
            self.load_error = "AI 配置读取失败，请重新配置"
            return AISettings()

    def key(self, settings):
        target = settings.credential_target
        return self.session_keys.get(target) or self.vault.get(target)

    def resolve_key(self, settings, draft):
        key = draft.strip() or self.key(settings)
        if not key:
            raise ValueError("请先填写 API Key")
        if (
            len(key.encode("utf-8")) > 2400
            or not key.isascii()
            or any(c.isspace() for c in key)
        ):
            raise ValueError("API Key 格式不正确，请粘贴完整 Key，不要包含空格或换行")
        if settings.provider == "qianwen_token_plan" and not key.startswith("sk-sp-"):
            raise ValueError(
                "Token Plan 需要 sk-sp- 开头的套餐专属 Key，请从套餐管理页面复制"
            )
        if settings.provider in {"qianwen", "qwen"} and key.startswith("sk-sp-"):
            raise ValueError(
                "这是套餐专属 Key，请选择对应的 Token Plan 服务商和套餐地址"
            )
        return key

    def save(self, settings, key):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        # Prepare a non-secret atomic file before modifying the credential vault.
        temporary.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            if key:
                if settings.remember:
                    self.vault.set(settings.credential_target, key)
                else:
                    self.vault.delete(settings.credential_target)
                self.session_keys[settings.credential_target] = key
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def forget(self, settings):
        self.vault.delete(settings.credential_target)
        self.session_keys.pop(settings.credential_target, None)
