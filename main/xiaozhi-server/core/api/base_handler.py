from aiohttp import web
from config.logger import setup_logging


class BaseHandler:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()

    def _add_cors_headers(self, response):
        """添加响应头

        [thinksmart] 不再发 Access-Control-Allow-Origin: *（和 Allow-Credentials）：那等于允许任何网站的
        网页在用户浏览器里调用 OTA、视觉接口并读到结果。设备（ESP32、本机客户端）不是浏览器，不看这些头。
        """
        response.headers["Access-Control-Allow-Headers"] = (
            "client-id, content-type, device-id, authorization"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"

    async def handle_options(self, request):
        """处理OPTIONS请求，添加CORS头信息"""
        response = web.Response(body=b"", content_type="text/plain")
        self._add_cors_headers(response)
        # 添加允许的方法
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
