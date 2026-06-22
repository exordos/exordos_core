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

from restalchemy.api import routes

from exordos_core.user_api.repo.api import controllers


class RepositoryRefreshActionRoute(routes.Action):
    """Handler for /v1/repo/repositories/<uuid>/actions/refresh/invoke endpoint"""

    __controller__ = controllers.RepositoryController


class RepositoryUploadActionRoute(routes.Action):
    """Handler for /v1/repo/repositories/<uuid>/actions/upload/invoke endpoint"""

    __controller__ = controllers.RepositoryController


class RepositoryRoute(routes.Route):
    """Handler for /v1/repo/repositories/ endpoint"""

    __controller__ = controllers.RepositoryController

    refresh = routes.action(RepositoryRefreshActionRoute, invoke=True)
    upload = routes.action(RepositoryUploadActionRoute, invoke=True)


class RepoElementInstallActionRoute(routes.Action):
    """Handler for /v1/repo/elements/<uuid>/actions/install/invoke endpoint"""

    __controller__ = controllers.RepoElementController


class RepoElementUninstallActionRoute(routes.Action):
    """Handler for /v1/repo/elements/<uuid>/actions/uninstall/invoke endpoint"""

    __controller__ = controllers.RepoElementController


class RepoElementUpgradeActionRoute(routes.Action):
    """Handler for /v1/repo/elements/<uuid>/actions/upgrade/invoke endpoint"""

    __controller__ = controllers.RepoElementController


class RepoElementEditActionRoute(routes.Action):
    """Handler for /v1/repo/elements/<uuid>/actions/edit/invoke endpoint"""

    __controller__ = controllers.RepoElementController


class RepoElementRoute(routes.Route):
    """Handler for /v1/repo/elements/ endpoint"""

    __controller__ = controllers.RepoElementController
    __allow_methods__ = [routes.GET, routes.FILTER, routes.DELETE]

    install = routes.action(RepoElementInstallActionRoute, invoke=True)
    uninstall = routes.action(RepoElementUninstallActionRoute, invoke=True)
    upgrade = routes.action(RepoElementUpgradeActionRoute, invoke=True)
    edit = routes.action(RepoElementEditActionRoute, invoke=True)


class RepoRoute(routes.Route):
    """Handler for /v1/repo/ endpoint"""

    __controller__ = controllers.RepoProxyController
    __allow_methods__ = [routes.FILTER]

    repositories = routes.route(RepositoryRoute)
    elements = routes.route(RepoElementRoute)
