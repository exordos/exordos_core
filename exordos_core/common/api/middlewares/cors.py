#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_config import cfg
from restalchemy.api import middlewares
from webob import dec

ALLOWED_ORIGINS_OPT = cfg.ListOpt(
    "allowed_origins",
    default=["*"],
    help="List of allowed CORS origins",
)

CORS_OPT_GROUP = cfg.OptGroup("cors")
CORS_OPTS = [ALLOWED_ORIGINS_OPT]
BASE_RESPONSE_HEADERS = {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": (
        "Authorization, Content-Type, X-OTP-Token, X-Requested-With, Accept, Origin"
    ),
    "Access-Control-Max-Age": "3600",
}


def register_cors_opts(conf):
    conf.register_group(CORS_OPT_GROUP)
    conf.register_opts(CORS_OPTS, group=CORS_OPT_GROUP)


class CORSMiddleware(middlewares.Middleware):
    def __init__(self, application, allowed_origins=None):
        super().__init__(application)
        self.allowed_origins = allowed_origins or []

    @dec.wsgify
    def __call__(self, req):
        origin = req.headers.get("Origin", "")

        if req.method == "OPTIONS" and self._is_origin_allowed(origin):
            return req.ResponseClass(
                status=200,
                headers=self._cors_headers(origin),
            )

        response = req.get_response(self.application)

        if self._is_origin_allowed(origin):
            for key, value in self._cors_headers(origin).items():
                response.headers.add(key, value)

        return response

    def _is_origin_allowed(self, origin):
        if not origin:
            return False
        return origin in self.allowed_origins or "*" in self.allowed_origins

    @staticmethod
    def _cors_headers(origin):
        headers = BASE_RESPONSE_HEADERS.copy()
        headers["Access-Control-Allow-Origin"] = origin
        return headers
