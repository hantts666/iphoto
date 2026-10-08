"""Qt network transport and credential lifecycle. Protocols are in ai_protocol."""

from dataclasses import asdict, replace
from hashlib import sha256
import json
import time
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from .ai_settings import AISettings, PROVIDERS, SettingsStore
from .engine import Recipe
from .photo_strategy import parse_review as parse_photo_review
from .matte_review import parse_review as parse_matte_review
from .matte_points import parse_points as parse_matte_points
from .ai_protocol import build_payload, image_data_url, parse_auto, parse_plan
from .ai_mask_refinement import PointLocationError, parse_points
from .ai_mask_review import parse_review, parse_verification
from .ai_tasks import parse_selection, parse_regions
from .ai_repair import parse_repair_spots
from .scene import parse_scene, parse_targets


class AIController(QObject):
    changed = Signal()
    progressChanged = Signal()
    planReady = Signal(object, int)
    failure = Signal(str)
    maskPointsUnavailable = Signal(int)
    requestStarted = Signal()

    def __init__(self, parent=None, store=None):
        super().__init__(parent)
        self.store = store if store is not None else SettingsStore()
        self.settings = self.store.load()
        self._reply = None
        self._context = None
        self._retry_context = None
        self._request_started = 0.0
        self._message = self.store.load_error
        self._error = bool(self._message)
        self._connection = "尚未测试"
        self._photo_sent = False
        self._tested_signature = ""
        self._has_key = False
        self._refresh_key()
        self.manager = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(
            lambda: self._abort(
                f"请求超时（{self.timer.interval() // 1000} 秒），请重试或检查网络；参数未改变"
            )
        )
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._restart)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(1000)
        self._progress_timer.timeout.connect(self.progressChanged.emit)

    def _refresh_key(self):
        try:
            self._has_key = bool(self.store.key(self.settings))
        except ValueError as exc:
            self._has_key = False
            self._message, self._error = str(exc), True

    @Property("QVariantList", constant=True)
    def providers(self):
        return PROVIDERS

    @Property("QVariantMap", notify=changed)
    def config(self):
        return asdict(self.settings)

    @Property(bool, notify=changed)
    def enabled(self):
        return self.settings.enabled

    @Property(bool, notify=changed)
    def ready(self):
        return self._has_key and self.settings.enabled

    @Property(bool, notify=changed)
    def busy(self):
        return self._reply is not None or self._retry_context is not None

    @Property(str, notify=progressChanged)
    def requestProgress(self):
        if self._retry_context is not None:
            seconds = max(0, (self._retry_timer.remainingTime() + 999) // 1000)
            attempt = self._retry_context["attempt"] + 2
            return f"网络连接不稳定，{seconds} 秒后重试（第 {attempt}/{self.MAX_RETRIES + 1} 次）· 可取消"
        if self._reply is None or self._context is None:
            return ""
        elapsed = max(0, int(time.monotonic() - self._request_started))
        if self._context.get("validation_retry"):
            phase = "AI 首次回复未通过校验，正在修正"
        elif self._context["workspace"].get("_grounding_label"):
            phase = "正在精定位：" + self._context["workspace"]["_grounding_label"]
        else:
            task = {"scene": "分析画面", "selection": "定位范围", "targets": "选择对象",
                    "photo_review": "3/4 成片效果检查", "matte_review":"4/4 抠图效果检查", "matte_points":"4/4 核对发丝落点", "regions": "规划分区", "advice": "修图建议", "auto": "智能修图",
                    "mask_points": "定位五官误选", "mask_review": "复查修正范围",
                    "mask_validate": "核对是否误删真实五官",
                    'mask_restore_validate': '核对新增五官覆盖',
                    'mask_reselect_validate': '核对完整五官与范围变化',
                    "repair": "检查局部瑕疵"}.get(self._context["mode"], "AI 修图")
            phase = task + ("：正在接收 AI 回复" if self._context["body"] else "：请求已发送，等待 AI 回应")
        return f"{phase} · 已等待 {elapsed} 秒 · 可取消"

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def isError(self):
        return self._error

    @Property(bool, constant=True)
    def canRemember(self):
        return self.store.vault.available

    @Property(str, notify=changed)
    def badge(self):
        if self.busy:
            return "AI 请求中…"
        if not self.enabled:
            return "本地规则模式"
        if not self.ready:
            return "AI 未配置 · 填写 Key"
        return self.settings.model + " · " + self._connection

    @Property(str, notify=changed)
    def privacyStatus(self):
        return "本次会话已发送照片缩略图" if self._photo_sent else "本次会话未发送照片"

    @Slot(str, str, result=bool)
    def hasKey(self, provider, base_url):
        try:
            settings = AISettings.validated(provider, base_url, "key-check")
            return bool(self.store.key(settings))
        except ValueError:
            return False

    def _show(self, message, error=False):
        self._message, self._error = message, error
        self.changed.emit()

    def _draft(self, provider, base_url, model, remember, enabled, key):
        settings = AISettings.validated(provider, base_url, model, enabled, remember)
        secret = self.store.resolve_key(settings, key) if enabled else ""
        return settings, secret

    @staticmethod
    def _signature(settings, secret):
        return sha256(
            f"{settings.provider}\n{settings.base_url}\n{settings.model}\n{secret}".encode()
        ).hexdigest()

    @Slot(str, str, str, str, bool, bool, result=bool)
    def save(self, provider, base_url, model, key, remember, enabled):
        if self.busy:
            self._show("请先等待或取消当前 AI 请求", True)
            return False
        try:
            if enabled:
                settings, secret = self._draft(
                    provider, base_url, model, remember, True, key
                )
            else:
                settings, secret = replace(self.settings, enabled=False), ""
            self.store.save(settings, secret)
            self.settings = settings
            self._connection = (
                "已连接"
                if secret
                and self._signature(settings, secret) == self._tested_signature
                else "未测试"
            )
            self._refresh_key()
            self._show(
                "已保存。关闭设置后，输入要求并点击「AI 修图」。"
                if enabled
                else "已切换到本地规则模式，不会调用云端。"
            )
            return True
        except (ValueError, OSError) as exc:
            self._show(
                str(exc)
                if isinstance(exc, ValueError)
                else "配置文件保存失败，请检查目录权限",
                True,
            )
            return False

    @Slot(str, str, str, str)
    def testConnection(self, provider, base_url, model, key):
        if self.busy:
            return
        try:
            settings, secret = self._draft(provider, base_url, model, False, True, key)
            self._start(
                settings,
                secret,
                "连接测试：请返回 status=applied，recipe 保持当前参数，summary 简述测试图的主要颜色。",
                Recipe().to_dict(),
                [],
                image_data_url(),
                -1,
                True,
            )
        except (ValueError, OSError) as exc:
            self._show(
                str(exc) if isinstance(exc, ValueError) else "无法准备测试图", True
            )

    @Slot(str, str)
    def forgetKey(self, provider, base_url):
        if self.busy:
            return
        try:
            settings = AISettings.validated(provider, base_url, "key-check")
            self.store.forget(settings)
            self._refresh_key()
            self._connection = "尚未测试"
            self._show("已删除该接口保存的 Key")
        except (ValueError, OSError):
            self._show("删除 Key 失败，请检查接口地址或系统凭据库", True)

    def plan(
        self, text, recipe, locked, image_path, generation, mode="edit", workspace=None
    ):
        if self.busy:
            return False
        try:
            if not text.strip() or len(text) > 4000:
                raise ValueError("请填写 1～4000 字的修图要求")
            secret = self.store.resolve_key(self.settings, "")
            self._start(
                self.settings,
                secret,
                text,
                recipe,
                locked,
                "" if mode == "targets" else image_data_url(
                    image_path, (workspace or {}).get("_image_crop")
                ),
                generation,
                False,
                mode,
                workspace,
            )
            return True
        except (ValueError, OSError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "无法准备照片缩略图，请重新导入照片"
            )
            self._show(message, True)
            self.failure.emit(message)
            return False

    def _start(
        self,
        settings,
        secret,
        text,
        recipe,
        locked,
        image_url,
        generation,
        testing,
        mode="edit",
        workspace=None,
        validation_retry=0,
        attempt=0,
    ):
        if attempt == 0:
            self._request_started = time.monotonic()
        request = QNetworkRequest(QUrl(settings.base_url + "/chat/completions"))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", ("Bearer " + secret).encode("ascii"))
        request.setRawHeader(b"User-Agent", b"iPhoto/1.4.0")
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.ManualRedirectPolicy,
        )
        timeout_ms = 90_000 if mode in ("auto", "scene") else 60_000
        request.setTransferTimeout(timeout_ms)
        payload = build_payload(
            settings, text, recipe, locked, image_url, mode, workspace
        )
        context = {
            "settings": settings,
            "secret": secret,
            "recipe": dict(recipe),
            "locked": list(locked),
            "generation": generation,
            "testing": testing,
            "mode": mode,
            "request_text": text,
            "image_url": image_url,
            "workspace": workspace or {},
            "validation_retry": validation_retry,
            "attempt": attempt,
            "retry_args": (
                settings,
                secret,
                text,
                recipe,
                locked,
                image_url,
                generation,
                testing,
                mode,
                workspace or {},
                validation_retry,
            ),
            "started": time.monotonic(),
            "body": bytearray(),
            "abort": "",
        }
        self._context = context
        self._reply = self.manager.post(
            request, json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        reply = self._reply
        reply.readyRead.connect(lambda: self._collect(reply, context))
        reply.finished.connect(lambda: self._finish(reply, context))
        self.timer.start(timeout_ms)
        self._progress_timer.start()
        self.progressChanged.emit()
        if not testing:
            if mode != "targets":
                self._photo_sent = True
            self.requestStarted.emit()
        self._show(
            "正在测试图片输入与参数返回（使用内置测试图）…"
            if testing
            else {
                "scene": "AI 正在分析画面元素，最长等待 90 秒，可取消…",
                "targets": "AI 正在选择已识别对象，无需重新上传照片…",
                "selection": "AI 正在直接描绘目标轮廓…",
                "regions": "AI 正在规划分区图层…",
                "auto": "AI 正在判断调整范围并规划图层…",
                "repair": "AI 正在放大检查局部瑕疵并定位修复点…",
                "matte_review":"4/4 AI 正在对照原片、透明度与实际抠图，检查误选和灰边…可取消",
                "matte_points":"4/4 AI 正在放大核对发丝落点，检查是否误点背景…可取消",
                "mask_points": "AI 正在对照原图与蒙版，定位误选范围…",
                "mask_review": "AI 正在复查修正结果，检查残留误选…",
                "mask_validate": "AI 正在对比修改前后，核对是否误删真实五官…",
                'mask_restore_validate': 'AI 正在对比补选前后，核对新增覆盖是否属于目标五官…',
                'mask_reselect_validate': 'AI 正在核对完整可见五官与新增、减少范围…',
            }.get(mode, "AI 正在看图并生成修图参数…")
        )

    def _collect(self, reply, context):
        if reply is not self._reply:
            return
        context["body"].extend(bytes(reply.readAll()))
        self.progressChanged.emit()
        if len(context["body"]) > 2_000_000:
            self._abort("服务返回内容过大，已停止请求；参数未改变")

    def _finish(self, reply, context):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self.timer.stop()
        self._progress_timer.stop()
        context["body"].extend(bytes(reply.readAll()))
        self._reply = self._context = None
        try:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if context["abort"]:
                raise ValueError(context["abort"])
            if (status != 200 or reply.error() != QNetworkReply.NoError) and (
                self._maybe_retry(context, status, reply)
            ):
                return
            if status != 200:
                messages = {
                    400: "请求格式或模型不兼容，请确认模型支持图片和 JSON 输出",
                    401: "API Key 无效或与接口地域不匹配",
                    403: "没有该模型的调用权限，请检查账号和服务开通状态",
                    404: "接口地址或模型不存在，请检查 Base URL 和模型名称",
                    429: "调用限额、余额不足或请求过于频繁，请检查服务商控制台",
                }
                if context["settings"].provider == "qianwen_token_plan":
                    messages[401] = (
                        "Token Plan 专属 Key 无效或与套餐地址不匹配，请检查 sk-sp- Key"
                    )
                    messages[403] = (
                        "Token Plan 套餐未生效、已到期或模型不可用，请检查套餐控制台"
                    )
                    messages[429] = (
                        "Token Plan 套餐额度或速率已达上限，请查看套餐控制台；不会切换按量计费接口"
                    )
                if status:
                    raise ValueError(
                        f"HTTP {status}："
                        + messages.get(
                            status, "服务暂不可用或返回重定向，请检查接口后重试"
                        )
                    )
                raise ValueError(
                    "网络连接失败，请检查网络、代理和接口地址（HTTPS 证书必须有效）"
                )
            if reply.error() != QNetworkReply.NoError:
                raise ValueError("网络响应未完整接收，请重试；参数未改变")
            if len(context["body"]) > 2_000_000:
                raise ValueError("服务返回内容过大，参数未改变")
            decoded = (
                context["body"].decode("utf-8").replace(context["secret"], "[已隐藏]")
            )
            try:
                response = json.loads(decoded)
                if context["mode"] == "auto":
                    result = parse_auto(
                        response, context["recipe"], context["locked"],
                        context["workspace"].get("current_scope"),
                        context["workspace"].get("existing_layers"),
                        context["workspace"].get("current_display_enabled", True),
                        context["workspace"],
                    )
                elif context["mode"] == "photo_review":
                    result = parse_photo_review(response, context["workspace"])
                elif context["mode"] == "matte_review":
                    result = parse_matte_review(response, context['workspace'])
                elif context["mode"] == "matte_points":
                    result = parse_matte_points(response, context['workspace'])
                elif context["mode"] == "selection":
                    result = parse_selection(response)
                elif context["mode"] == "repair":
                    result = parse_repair_spots(response, context["workspace"])
                elif context["mode"] == "mask_points":
                    result = parse_points(response, context["workspace"])
                elif context["mode"] == "mask_review":
                    result = parse_review(response, context["workspace"])
                elif context["mode"] in ("mask_validate", "mask_restore_validate", "mask_reselect_validate"):
                    result = parse_verification(response)
                elif context["mode"] == "scene":
                    result = parse_scene(response)
                elif context["mode"] == "targets":
                    result = parse_targets(
                        response, context["workspace"].get("objects", [])
                    )
                elif context["mode"] == "regions":
                    result = parse_regions(response)
                else:
                    result = parse_plan(response, context["recipe"], context["locked"])
            except ValueError as exc:
                if self._retry_invalid_result(context, str(exc)):
                    return
                raise
            result["mode"] = context["mode"]
            elapsed = time.monotonic() - context["started"]
            self._tested_signature = self._signature(
                context["settings"], context["secret"]
            )
            if context["settings"] == self.settings:
                self._connection = "已连接"
            if context["testing"]:
                self._show(
                    f"连接成功 · {context['settings'].model} · {elapsed:.1f} 秒。图片输入与参数返回已通过，请保存设置。"
                )
            else:
                result["model"] = context["settings"].model
                self._show(f"AI 已返回 · {elapsed:.1f} 秒")
                self.planReady.emit(result, context["generation"])
        except (ValueError, UnicodeError) as exc:
            message = (
                "服务未返回有效的 JSON，请检查接口配置；参数未改变"
                if isinstance(exc, (json.JSONDecodeError, UnicodeError))
                else str(exc)
            )
            if not context["testing"] and context["mode"] == "mask_points" and isinstance(exc, PointLocationError):
                self._show("AI 点位未能可靠定位，正在改用原图区域复查…")
                self.maskPointsUnavailable.emit(context["generation"])
            else:
                self._connection = "请求未完成"
                self._show(message, True)
                if not context["testing"]:
                    self.failure.emit(message)
        finally:
            context["secret"] = ""
            context["body"].clear()
            reply.deleteLater()
            self.progressChanged.emit()
            self.changed.emit()

    def _retry_invalid_result(self, context, reason):
        # A second visual opinion on an unchanged final cutout is not a
        # format repair. Do not turn an invalid final verdict into acceptance
        # by asking the reviewer to judge the same pixels again.
        if context['mode']=='matte_review' and context['workspace'].get('revision')==1:
            return False
        if (
            context["testing"]
            or context["mode"] not in {"matte_points", "matte_review", "photo_review", "auto", "scene", "regions", "selection", "repair", "mask_points", "mask_review", "mask_validate", "mask_restore_validate", "mask_reselect_validate"}
            or context["validation_retry"]
            or context["abort"]
        ):
            return False
        workspace = dict(context["workspace"])
        workspace["_validation_feedback"] = reason[:200]
        self._start(
            context["settings"], context["secret"], context["request_text"],
            context["recipe"], context["locked"], context["image_url"],
            context["generation"], False, context["mode"], workspace,
            validation_retry=1,
        )
        self._show("AI 首次回复未通过校验，正在自动修正一次…")
        return True

    MAX_RETRIES = 2

    @staticmethod
    def _retryable(status, reply):
        if status is None:
            return reply.error() in (
                QNetworkReply.ConnectionRefusedError,
                QNetworkReply.RemoteHostClosedError,
                QNetworkReply.HostNotFoundError,
                QNetworkReply.TimeoutError,
                QNetworkReply.TemporaryNetworkFailureError,
                QNetworkReply.NetworkSessionFailedError,
                QNetworkReply.ProxyConnectionRefusedError,
                QNetworkReply.ProxyConnectionClosedError,
                QNetworkReply.ProxyTimeoutError,
                QNetworkReply.UnknownNetworkError,
            )
        return status in (408, 502, 503, 504)

    def _maybe_retry(self, context, status, reply):
        if context["attempt"] >= self.MAX_RETRIES or not self._retryable(
            status, reply
        ):
            return False
        delay = 1500 * 2 ** context["attempt"]
        self._retry_context = context
        self._retry_timer.start(delay)
        self._progress_timer.start()
        self.progressChanged.emit()
        self._show(
            f"网络瞬时错误，{delay / 1000:.1f} 秒后第 {context['attempt'] + 1} 次重试…"
        )
        return True

    def _restart(self):
        context = self._retry_context
        if context is None:
            return
        self._retry_context = None
        if self._reply is not None:
            return  # A newer request superseded this stale retry.
        self._start(*context["retry_args"], attempt=context["attempt"] + 1)

    def _abort(self, reason):
        if self._reply is not None:
            self._context["abort"] = reason
            self._reply.abort()
        elif self._retry_context is not None:
            context = self._retry_context
            self._retry_context = None
            self._retry_timer.stop()
            self._progress_timer.stop()
            self._connection = "请求未完成"
            self._show(reason, True)
            if not context["testing"]:
                self.failure.emit(reason)
            self.progressChanged.emit()

    @Slot()
    def cancel(self):
        self._abort("已取消 AI 请求，参数未改变")

    def close(self):
        self.cancel()
        self.store.session_keys.clear()
