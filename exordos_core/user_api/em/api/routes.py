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

from exordos_core.user_api.em.api import controllers


class ManifestUpgradeActionRoute(routes.Action):
    """Handler for /v1/em/manifests/<uuid>/actions/upgrade/invoke endpoint"""

    __controller__ = controllers.ManifestController


class ManifestInstallActionRoute(routes.Action):
    """Handler for /v1/em/manifests/<uuid>/actions/install/invoke endpoint"""

    __controller__ = controllers.ManifestController


class ManifestUninstallActionRoute(routes.Action):
    """Handler for /v1/em/manifests/<uuid>/actions/uninstall/invoke endpoint"""

    __controller__ = controllers.ManifestController


class ManifestValidateActionRoute(routes.Action):
    """Handler for /v1/em/manifests/<uuid>/actions/validate/invoke endpoint"""

    __controller__ = controllers.ManifestController


class ManifestRoute(routes.Route):
    """Handler for /v1/em/manifests/ endpoint"""

    __controller__ = controllers.ManifestController

    install = routes.action(ManifestInstallActionRoute, invoke=True)
    upgrade = routes.action(ManifestUpgradeActionRoute, invoke=True)
    uninstall = routes.action(ManifestUninstallActionRoute, invoke=True)
    validate = routes.action(ManifestValidateActionRoute, invoke=False)


class ElementResourceRoute(routes.Route):
    """Handler for /v1/em/elements/<uuid>/resources/ endpoint"""

    __controller__ = controllers.ElementResourceController


class ResourceAllRoute(routes.Route):
    """Handler for /v1/resources/[<id>] endpoint"""

    __controller__ = controllers.ResourceAllController
    __allow_methods__ = [
        routes.FILTER,
        routes.GET,
    ]


class ElementExportRoute(routes.Route):
    """Handler for /v1/em/elements/<uuid>/exports/ endpoint"""

    __controller__ = controllers.ElementExportController


class ExportAllRoute(routes.Route):
    """Handler for /v1/exports/[<id>] endpoint"""

    __controller__ = controllers.ExportAllController
    __allow_methods__ = [
        routes.FILTER,
        routes.GET,
    ]


class ElementImportRoute(routes.Route):
    """Handler for /v1/em/elements/<uuid>/imports/ endpoint"""

    __controller__ = controllers.ElementImportController


class ImportAllRoute(routes.Route):
    """Handler for /v1/imports/[<id>] endpoint"""

    __controller__ = controllers.ImportAllController
    __allow_methods__ = [
        routes.FILTER,
        routes.GET,
    ]


class ElementSetProfileActionRoute(routes.Action):
    """Handler for /v1/em/elements/<uuid>/actions/set_profile/invoke endpoint"""

    __controller__ = controllers.ElementController


class ElementClearProfileActionRoute(routes.Action):
    """Handler for /v1/em/elements/<uuid>/actions/clear_profile/invoke endpoint"""

    __controller__ = controllers.ElementController


class ElementRoute(routes.Route):
    """Handler for /v1/em/elements/<uuid>/ endpoint"""

    __controller__ = controllers.ElementController

    resources = routes.route(ElementResourceRoute, resource_route=True)
    exports = routes.route(ElementExportRoute, resource_route=True)
    imports = routes.route(ElementImportRoute, resource_route=True)
    set_profile = routes.action(ElementSetProfileActionRoute, invoke=True)
    clear_profile = routes.action(ElementClearProfileActionRoute, invoke=True)


class ServicesRoute(routes.Route):
    """Handler for /v1/em/services/ endpoint"""

    __controller__ = controllers.ServicesController


class ElementManagerRoute(routes.Route):
    """Handler for /v1/em/ endpoint"""

    __controller__ = controllers.ElementManagerController
    __allow_methods__ = [routes.FILTER]

    manifests = routes.route(ManifestRoute)
    elements = routes.route(ElementRoute)
    resources = routes.route(ResourceAllRoute)
    exports = routes.route(ExportAllRoute)
    imports = routes.route(ImportAllRoute)
    services = routes.route(ServicesRoute)
