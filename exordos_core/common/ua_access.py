#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#        https://www.apache.org/licenses/LICENSE-2.0

from http import HTTPStatus

from restalchemy.common import exceptions


class AgentAccessDenied(exceptions.RestAlchemyException):
    code = HTTPStatus.FORBIDDEN
    message = "The agent is not registered to the requesting node."


def require_request_node(context, node) -> None:
    if str(context.encryption_information.node_uuid) != str(node):
        raise AgentAccessDenied()
