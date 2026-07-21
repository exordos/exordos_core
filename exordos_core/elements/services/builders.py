#    Copyright 2025 Genesis Corporation.
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

import logging

from gcl_looper.services import basic
from restalchemy.common import contexts

from exordos_core.elements.dm import models

LOG = logging.getLogger(__name__)


class ElementManagerBuilder(basic.BasicService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ):
        super().__init__(iter_min_period, iter_pause)
        self._element_engine = models.element_engine
        self._first_step = True

    def _actualize_target_resources(self):
        # Delete outdated resources that do not have a corresponding EM
        for info in models.OutdatedResources.objects.get_all():
            if info.em_resource is None:
                info.target_resource.delete()
                LOG.info(" Resource %s has been deleted", info.target_resource)

        for resource in self._element_engine.get_resources():
            resource.actualize()

    def _actualize_statuses(self, session):
        incorrect_resource_statuses = (
            models.ResourceIncorrectStatusesView.objects.get_all()
        )
        for em_status_model in incorrect_resource_statuses:
            LOG.info(
                "Actualizing status for resource (%s): %s -> %s...",
                em_status_model.uuid,
                em_status_model.current_status,
                em_status_model.actual_status,
            )
            em_status_model.actualize_status(session)

        incorrect_element_statuses = (
            models.ElementIncorrectStatusesView.objects.get_all()
        )
        for em_status_model in incorrect_element_statuses:
            LOG.info(
                "Actualizing status for element (%s): %s -> %s...",
                em_status_model.name,
                em_status_model.api_status,
                em_status_model.actual_status,
            )
            em_status_model.actualize_status(session)

    def _iteration(self):
        with contexts.Context().session_manager() as session:
            if self._first_step:
                self._element_engine.load_from_database()
                self._first_step = True  # Disable optimization
            self._actualize_target_resources()
            self._actualize_statuses(session)
            LOG.debug("Starting iteration")
