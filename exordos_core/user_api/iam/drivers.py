# Copyright 2025 Genesis Corporation
#
# All Rights Reserved.
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

from gcl_iam import algorithms as iam_algorithms
from gcl_iam import drivers as iam_drivers

from exordos_core.user_api.iam.dm import models


class DirectDriver(iam_drivers.AbstractAuthDriver):
    def get_introspection_info(self, token_info, otp_code=None):
        token = models.Token.my(token_info=token_info)
        token.validate_expiration()
        token.validate_generation(token_info=token_info)
        return token.introspect(
            token_info=token_info, otp_code=otp_code
        ).get_response_body()

    def get_algorithm(self, token_info):
        token = models.Token.my(token_info=token_info)
        signature_algorithm = token.iam_client.signature_algorithm

        if signature_algorithm.kind == iam_algorithms.ALGORITHM_HS256:
            secret_value = signature_algorithm.secret.value
            previous_secret_value = (
                None
                if signature_algorithm.previous_secret is None
                else signature_algorithm.previous_secret.value
            )
            return iam_algorithms.HS256(
                key=secret_value,
                previous_key=previous_secret_value,
            )

        elif signature_algorithm.kind == iam_algorithms.ALGORITHM_RS256:
            secret = signature_algorithm.secret
            previous_secret = signature_algorithm.previous_secret

            return iam_algorithms.RS256(
                private_key=secret.private_key,
                public_key=secret.public_key,
                previous_public_key=(
                    None if previous_secret is None else previous_secret.public_key
                ),
            )

        raise ValueError(f"Unknown signature algorithm: {signature_algorithm.kind}")
