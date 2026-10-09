"""Qwen Image editing transport, separate from the chat/analysis model."""

import json
import time
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

from PIL import Image
from PySide6.QtCore import QObject, Property, Signal, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


def endpoint(settings):
    if settings.provider not in ('qianwen_token_plan', 'qianwen', 'qwen'):
        raise ValueError('当前服务商尚未接入选区图像编辑接口')
    parts = urlsplit(settings.base_url)
    if not parts.path.endswith('/compatible-mode/v1'):
        raise ValueError('当前地址不支持选区图像编辑')
    return urlunsplit((parts.scheme, parts.netloc, '/api/v1/services/aigc/multimodal-generation/generation', '', ''))


class ImageEditController(QObject):
    changed = Signal()
    completed = Signal(object, str, int)
    failure = Signal(str)

    def __init__(self, ai, parent=None):
        super().__init__(parent)
        self.ai = ai
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    @Property(bool, notify=changed)
    def busy(self):
        return self.reply is not None

    @Property(str, notify=changed)
    def progress(self):
        if not self.busy:
            return ''
        task = '接收生成图像' if self.stage == 'download' else 'AI正在选区内生成精修像素'
        return f'{task} · 已等待 {int(time.monotonic()-self.started)} 秒 · 可取消'

    def _tick(self):
        if self.busy and time.monotonic()-self.started > 300:
            self.cancel('选区图像编辑超时，照片未改变')
        self.changed.emit()

    def start(self, image_url, prompt, size, token, generation, *, scope_url=None):
        try:
            if self.busy:
                return False
            from .generation_size import validate_size
            from .generation_scope import SCOPE_PROMPT, validate_scope_reference
            size = validate_size(size)
            validate_scope_reference(image_url, scope_url)
            target = endpoint(self.ai.settings)
            secret = self.ai.store.resolve_key(self.ai.settings, '')
            payload = {'model': 'qwen-image-3.0-pro', 'input': {'messages': [{'role': 'user', 'content': [
                {'image': image_url}, {'image': scope_url},
                {'text': prompt + SCOPE_PROMPT + '\n保持输入照片的构图、比例、所有内容位置和人物身份。只按要求精修。除用户明确指定的变化外，保留五官形状、年龄、表情、衣物、场景。保留皮肤纹理和原有光影，避免面具边缘、假皮肤和过度美颜。'}]}]},
                'parameters': {'size': f'{size[0]}*{size[1]}', 'n': 1, 'watermark': False, 'prompt_extend': False}}
            request = QNetworkRequest(QUrl(target))
            request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
            request.setRawHeader(b'Authorization', ('Bearer '+secret).encode('ascii'))
            self.token, self.generation, self.started = token, generation, time.monotonic()
            self.output_size = size
            self.cancel_reason = ''
            self._send(request, json.dumps(payload, ensure_ascii=False).encode(), 'generate')
            secret = ''
            self.timer.start()
            self.ai._photo_sent = True
            self.ai.changed.emit()
            return True
        except (ValueError, UnicodeError) as exc:
            self.failure.emit(str(exc))
            return False

    def _send(self, request, body, stage):
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setTransferTimeout(240000 if stage == 'generate' else 60000)
        self.stage, self.body = stage, bytearray()
        reply = self.network.post(request, body) if body is not None else self.network.get(request)
        self.reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finish(reply))
        self.changed.emit()

    def _read(self, reply):
        if reply != self.reply:
            return
        self.body.extend(bytes(reply.readAll()))
        if len(self.body) > (16*1024*1024 if self.stage == 'download' else 2_000_000):
            self.cancel('生成图像响应过大，照片未改变')

    def _finish(self, reply):
        if reply != self.reply:
            return
        self._read(reply)
        data, stage = bytes(self.body), self.stage
        self.reply = None
        try:
            if self.cancel_reason:
                raise ValueError(self.cancel_reason)
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if status != 200 or reply.error() != QNetworkReply.NoError:
                raise ValueError(f'选区图像编辑请求失败（HTTP {status or "网络错误"}），照片未改变；请检查套餐和网络')
            if stage == 'generate':
                result = json.loads(data)
                urls = [part['image'] for choice in result.get('output', {}).get('choices', [])
                        for part in choice.get('message', {}).get('content', []) if part.get('image')]
                if len(urls) != 1 or not isinstance(urls[0], str):
                    raise ValueError('图像编辑没有返回单张有效图像，照片未改变')
                parts = urlsplit(urls[0])
                if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
                    raise ValueError('生成图像地址无效，照片未改变')
                # The signed image URL never receives the API credential.
                self._send(QNetworkRequest(QUrl(urls[0])), None, 'download')
                return
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width*image.height > 2048*2048:
                    raise ValueError('生成图像格式或分辨率无效，照片未改变')
                if abs((image.width/image.height)/(self.output_size[0]/self.output_size[1])-1) > .02:
                    raise ValueError('生成图像比例不匹配，照片未改变')
                image.load()
                output = image.convert('RGB')
            self.completed.emit(output, self.token, self.generation)
        except (ValueError, OSError, TypeError, KeyError, Image.DecompressionBombError):
            message = self.cancel_reason or '选区图像编辑未完整完成，照片未改变'
            # Error details from a provider may echo signed URLs/credentials.
            if self.cancel_reason:
                message = self.cancel_reason
            elif reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) != 200:
                message = f'选区图像编辑失败（HTTP {reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or "网络错误"}），照片未改变'
            self.failure.emit(message)
        finally:
            reply.deleteLater()
            if not self.busy:
                self.timer.stop()
                self.body.clear()
            self.changed.emit()

    def cancel(self, reason='已取消选区图像编辑，照片未改变'):
        self.cancel_reason = reason
        if self.reply is not None:
            self.reply.abort()

    def close(self):
        self.cancel()
