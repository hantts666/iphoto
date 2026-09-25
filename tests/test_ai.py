import base64
import ctypes
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import sys
import threading
import time
from uuid import uuid4

import pytest
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from iphoto.ai import AIController, build_payload, image_data_url, parse_plan
from iphoto.ai_settings import AISettings, SettingsStore, WindowsCredentialVault, normalize_url
from iphoto.app import Editor
from iphoto.engine import Recipe


def wait_for(condition, seconds=12):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QGuiApplication.processEvents()
        if condition(): return
        QTest.qWait(15)
    raise AssertionError("Qt network/worker operation timed out")


def completion(recipe=None, status="applied", summary="提亮阴影，保留自然色彩。"):
    plan = {"status": status, "recipe": recipe or Recipe(exposure=.25, shadows=18).to_dict(), "summary": summary}
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan, ensure_ascii=False)}}]}


@contextmanager
def mock_api(body=None, status=200, delay=0):
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), payload))
            if delay:
                release.wait(delay)
            chosen = body(payload) if callable(body) else completion() if body is None else body
            encoded = json.dumps(chosen, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            if status == 302: self.send_header("Location", "https://example.com/should-never-follow")
            self.end_headers()
            try: self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def configure(controller, url):
    assert controller.save("custom", url, "test-vision", "sk-test-only-not-a-real-key", False, True)


def test_key_storage_persistence_binding_delete_and_session_only(ai_store):
    settings = AISettings.validated("qwen", "https://example.com/v1/", "vision")
    ai_store.save(settings, "sk-synthetic-key")
    assert "sk-synthetic" not in ai_store.path.read_text()
    restarted = SettingsStore(ai_store.directory, ai_store.vault)
    assert restarted.key(restarted.load()) == "sk-synthetic-key"
    assert not restarted.key(replace(settings, base_url="https://different.example/v1"))
    assert not restarted.key(replace(settings, provider="custom"))
    restarted.save(replace(settings, remember=False), "sk-session")
    assert restarted.key(settings) == "sk-session"
    assert not SettingsStore(ai_store.directory, ai_store.vault).key(settings)
    restarted.forget(settings)
    assert not restarted.key(settings)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential API")
def test_real_windows_vault_roundtrip():
    vault = WindowsCredentialVault()
    target = "iPhoto/test/" + uuid4().hex
    try:
        assert not vault.get(target)
        try:
            vault.set(target, "synthetic-credential-for-automated-test")
        except ValueError:
            if ctypes.get_last_error() == 1312:
                pytest.skip("沙箱没有 Windows 登录凭据会话；此项需在真实登录会话单独验证")
            raise
        assert vault.get(target) == "synthetic-credential-for-automated-test"
        vault.delete(target)
        assert not vault.get(target)
    finally:
        vault.delete(target)


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=secret", "file:///tmp/api", "https://example.com:bad/v1"])
def test_unsafe_or_ambiguous_url_rejected(url):
    with pytest.raises(ValueError): normalize_url(url)


def test_url_and_protocol_payload():
    assert normalize_url(" https://API.example.com:443/v1/chat/completions/ ") == "https://api.example.com/v1"
    for provider in ("openai", "qwen", "qianwen", "qianwen_token_plan", "custom"):
        settings = AISettings(provider=provider)
        payload = build_payload(settings, "提亮", Recipe().to_dict(), [], "data:image/jpeg;base64,dummy")
        assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:")
        assert payload["response_format"]["type"] == ("json_schema" if provider == "openai" else "json_object")
        assert ("enable_thinking" in payload) == (provider in {"qwen", "qianwen", "qianwen_token_plan"})


def test_qianwen_token_plan_endpoint_and_credential_isolation(ai_store):
    settings = AISettings()
    assert settings.provider == "qianwen_token_plan"
    assert settings.base_url == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
    assert settings.model == "qwen3.8-flash"
    with pytest.raises(ValueError, match="sk-sp-"):
        ai_store.resolve_key(settings, "sk-ordinary-pay-as-you-go-key")
    assert ai_store.resolve_key(settings, "sk-sp-test-only") == "sk-sp-test-only"
    with pytest.raises(ValueError, match="套餐专属"):
        AISettings.validated(settings.provider, "https://dashscope.aliyuncs.com/compatible-mode/v1", settings.model)
    with pytest.raises(ValueError, match="套餐专属"):
        ai_store.resolve_key(AISettings.validated("qianwen", "https://maas.qianwenaiapi.com/compatible-mode/v1", settings.model), "sk-sp-test-only")
    ai_store.save(settings, "sk-sp-test-only")
    assert not ai_store.key(AISettings.validated("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", settings.model))


def test_thumbnail_strips_metadata_and_limits_size(tmp_path):
    path = tmp_path / "private-image.jpg"
    source = Image.new("RGB", (2400, 1600), (100, 140, 160))
    exif = Image.Exif(); exif[315] = "private-name"
    source.save(path, exif=exif)
    url = image_data_url(path)
    encoded = base64.b64decode(url.split(",", 1)[1])
    assert b"private-name" not in encoded
    with Image.open(BytesIO(encoded)) as thumb:
        assert max(thumb.size) == 1280
        assert not thumb.getexif()


def test_plan_validation_manual_lock_and_unsupported():
    current = Recipe(exposure=.7).to_dict()
    assert parse_plan(completion(), current, ["exposure"])["recipe"]["exposure"] == .7
    assert parse_plan(completion(status="unsupported"), current, [])["recipe"] == current
    for invalid in ({"extra": 1}, {**Recipe().to_dict(), "warmth": 900}, {**Recipe().to_dict(), "exposure": True}, {**Recipe().to_dict(), "contrast": float("nan")}):
        with pytest.raises(ValueError): parse_plan(completion(invalid), current, [])
    broken = completion(); broken["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError): parse_plan(broken, current, [])
    with pytest.raises(ValueError): parse_plan({"unexpected": "HTML instead of JSON"}, current, [])


def test_connection_probe_save_and_real_editor_cloud_undo_export(qt_app, ai_store, tmp_path):
    with mock_api() as (url, requests):
        editor = Editor(ai_store=ai_store)
        try:
            editor.ai.testConnection("custom", url, "test-vision", "sk-test-only-not-a-real-key")
            wait_for(lambda: not editor.ai.busy)
            assert "连接成功" in editor.ai.message
            assert "未发送" in editor.ai.privacyStatus
            configure(editor.ai, url)
            assert "已连接" in editor.ai.badge
            path = tmp_path / "original.png"
            Image.new("RGB", (320, 220), (90, 100, 140)).save(path)
            before = path.read_bytes()
            editor.openImage(str(path))
            wait_for(lambda: editor.hasImage and editor._active is None and not editor._pending_render)
            editor.setParameter("exposure", .7)
            editor.finishGesture()
            editor.applyDescription("暗部亮一点，色彩自然")
            assert editor.busy
            wait_for(lambda: not editor.busy and editor.parameters["shadows"] == 18)
            wait_for(lambda: editor._active is None and not editor._pending_render and not editor._timer.isActive())
            assert editor.parameters["exposure"] == .7
            assert "已发送照片缩略图" in editor.ai.privacyStatus
            assert len(requests) == 2
            assert requests[-1][0] == "/v1/chat/completions"
            assert requests[-1][1] == "Bearer sk-test-only-not-a-real-key"
            context = json.loads(requests[-1][2]["messages"][1]["content"][0]["text"])
            assert context["locked"] == ["exposure"]
            editor.undo()
            assert editor.parameters["shadows"] == 0
            editor.redo()
            target = tmp_path / "cloud-edited.png"
            editor.exportImage(str(target))
            wait_for(lambda: target.exists() and not editor.busy)
            assert path.read_bytes() == before
            assert Image.open(target).size == (320, 220)
        finally:
            editor.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 302])
def test_http_errors_do_not_apply_or_expose_key(qt_app, ai_store, status):
    with mock_api({"error": {"message": "sk-test-only-not-a-real-key"}}, status=status) as (url, requests):
        ai = AIController(store=ai_store)
        configure(ai, url)
        plans = []; ai.planReady.connect(lambda *args: plans.append(args))
        ai.testConnection("custom", url, "test-vision", "")
        wait_for(lambda: not ai.busy)
        assert ai.isError
        assert str(status) in ai.message
        assert "sk-test" not in ai.message
        assert not plans and len(requests) == 1
        ai.close()


@pytest.mark.parametrize("action", ["cancel", "timeout"])
def test_cancel_and_timeout_leave_parameters_unchanged(qt_app, ai_store, action):
    with mock_api(delay=2) as (url, requests):
        ai = AIController(store=ai_store)
        configure(ai, url)
        results = []; ai.planReady.connect(lambda *args: results.append(args))
        ai.testConnection("custom", url, "test-vision", "")
        wait_for(lambda: bool(requests))
        if action == "timeout": ai.timer.start(80)  # Override after the mode-specific production deadline is configured.
        if action == "cancel": ai.cancel()
        wait_for(lambda: not ai.busy)
        assert ("取消" if action == "cancel" else "超时") in ai.message
        assert not results
        ai.close()


def test_missing_key_requests_settings_and_no_local_fallback(qt_app, ai_store, tmp_path):
    editor = Editor(ai_store=ai_store)
    try:
        requests = []; editor.aiSettingsRequested.connect(lambda: requests.append(True))
        path = tmp_path / "image.png"; Image.new("RGB", (30, 30)).save(path)
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and not editor._active)
        before = dict(editor.parameters)
        editor.applyDescription("提亮暗部")
        assert requests and editor.parameters == before
        assert editor._active is None
    finally:
        editor.close()
