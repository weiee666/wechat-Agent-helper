# -*- coding: utf-8 -*-
"""临时二维码网页：headless 服务器登录时，把 iLink 登录二维码用网页显示，
在另一台机器（你的电脑）浏览器打开、手机扫码。用标准库 http.server，SVG 无需 Pillow。

用法（见 ilink.login(qr_web=True)）：
    srv = QRWebServer(qr_content, port=8765); srv.start()
    ... 二维码刷新时 srv.update(new_content) ...
    srv.stop()
"""
from __future__ import annotations

import io
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qrcode
import qrcode.image.svg


def local_ip() -> str:
    """探测本机对外 IP（云服务器上是内网 IP，公网 IP 需用实例的公网地址）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _svg_bytes(content: str) -> bytes:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(content)
    qr.make(fit=True)
    # SvgPathImage：单个 <path> 的干净 SVG（无 svg: 命名空间前缀），
    # 既能作为 .svg 文件显示，也能直接 innerHTML 注入网页渲染。
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    # 去掉 <?xml ...?> 声明，便于前端 innerHTML 注入
    svg = re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
    return svg.encode("utf-8")


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>微信扫码登录</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:-apple-system,sans-serif;text-align:center;padding:32px;color:#222}}
 .qr{{width:280px;height:280px;margin:16px auto}}
 .hint{{color:#666;font-size:14px;line-height:1.6}}
</style>
<script>
 // 每 3 秒刷新二维码图片（应对过期自动换码）
 setInterval(function(){{
   document.getElementById('qr').src = '/qr.svg?t=' + Date.now();
 }}, 3000);
</script></head>
<body>
 <h2>用微信扫码登录任务助手</h2>
 <img id="qr" class="qr" src="/qr.svg?t=0" alt="二维码">
 <div class="hint">打开微信 → 扫一扫 → 扫描上面的二维码 → 在手机上确认<br>
 （二维码会自动刷新，扫不上稍等一下再试）</div>
</body></html>"""


class QRWebServer:
    def __init__(self, content: str, port: int = 8765) -> None:
        self._svg = _svg_bytes(content)
        self._lock = threading.Lock()
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    def update(self, content: str) -> None:
        with self._lock:
            self._svg = _svg_bytes(content)

    def _current(self) -> bytes:
        with self._lock:
            return self._svg

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def do_GET(self):
                if self.path.startswith("/qr.svg"):
                    body = server._current()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = _PAGE.format().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd = None
