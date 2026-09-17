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

import datetime
import logging

from gcl_looper.services import basic
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.user_api.iam.dm import models

LOG = logging.getLogger(__name__)


class TokenRenewalService(basic.BasicService):
    """Renews the tokens that renew themselves before they expire."""

    def _iteration(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        tokens = models.ManagedToken.objects.get_all(
            filters={"auto_renew": dm_filters.EQ(True)},
        )
        for token in tokens:
            if not token.needs_renewal(now):
                continue
            try:
                with contexts.Context().session_manager():
                    token.renew(now)
                LOG.info("Token %s renewed until %s", token.uuid, token.expiration_at)
            except Exception:
                LOG.exception("Failed to renew token %s", token.uuid)
