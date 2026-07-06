#    Copyright 2025-2026 Genesis Corporation.
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
import os
import uuid as sys_uuid

GLOBAL_SERVICE_NAME = "exordos_core"

SERVICE_PROJECT_ID = sys_uuid.UUID("00000000-0000-0000-0000-000000000000")
EM_PROJECT_ID = sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142")
EM_HIDDEN_PROJECT_ID = sys_uuid.UUID("12345670-6f3a-402e-acf8-0319d53eab58")
CORE_SET_UUID = sys_uuid.UUID("70c88222-b4d9-46c3-9340-aa5bfaaa4b94")
MAIN_SUBNET_UUID = sys_uuid.UUID("c910a7e1-61ae-4d56-bdd6-a59faa3cbda3")
VAR_CORE_IP_ADDRESS_UUID = sys_uuid.UUID("55814431-ede5-4c4e-abd6-e61600a3069b")
VAR_ROOT_DISK_UUID = sys_uuid.UUID("695f4e94-e46b-43ae-aa66-194044740912")
VAR_DATA_DISK_UUID = sys_uuid.UUID("cf92f6f5-b62f-4cc4-898c-5250a6608d51")
VALUE_CORE_IP_ADDRESS_UUID = sys_uuid.UUID("0225c5ed-07db-45fe-8154-2b8b9cae388a")
VAR_IAM_DEFAULT_CLIENT_UUID = sys_uuid.UUID("d23fa962-18b8-444f-81a0-027ece98fbdb")
VAR_IAM_DEFAULT_CLIENT_ID_UUID = sys_uuid.UUID("239e2a55-c3ad-4adc-85a8-3fa90b669556")
VAR_IAM_DEFAULT_CLIENT_SECRET_UUID = sys_uuid.UUID(
    "784c1f6d-f9e2-47e0-a3ba-16725854ac09"
)
NETWORK_UUID = "1d4f64db-817a-4862-a588-c9e950823cc1"
DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
DEFAULT_SQL_LIMIT = 100

WORK_DIR = "/var/lib/exordos"
DATA_DIR = os.path.join(WORK_DIR, "data")
NODE_UUID_PATH = os.path.join(WORK_DIR, "node-id")

DEFAULT_USER_API_HOST = "127.0.0.1"
DEFAULT_USER_API_PORT = 11010
DEFAULT_ROOT_ENDPOINT = f"http://{DEFAULT_USER_API_HOST}:{DEFAULT_USER_API_PORT}/v1/"

DEFAULT_GLOBAL_SALT = "FOy/2kwwdn0ig1QOq7cestqe"
DEFAULT_ADMIN_SALT = "d4JJ9QYuEEJxHCFja9FZskG4"
DEFAULT_CLIENT_UUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_CLIENT_ID = "GenesisCoreClientId"
DEFAULT_CLIENT_SECRET = "GenesisCoreSecret"
DEFAULT_HS256_JWKS_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


# Exordos Core Organization and Project Information
EXORDOS_CORE_ORGANIZATION_UUID = "11111111-1111-1111-1111-111111111111"
EXORDOS_CORE_ORGANIZATION_NAME = "Genesis Corporation"
EXORDOS_CORE_ORGANIZATION_DESCRIPTION = (
    "The organization serves as the central platform for all services"
    " and elements developed by Genesis Corporation."
)


# Exordos Core Default Roles
NEWCOMER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000001"
NEWCOMER_ROLE_NAME = "newcomer"
NEWCOMER_ROLE_DESCRIPTION = (
    "Default role for newly registered users. Provides basic system access "
    "and onboarding capabilities."
)

OWNER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000002"
OWNER_ROLE_NAME = "owner"
OWNER_ROLE_DESCRIPTION = (
    "Project ownership role. Grants full administrative privileges "
    "within a specific project. Automatically assigned during project "
    "creation process."
)


# ValuesStore variable UUIDs
VAR_CORE_IP_ADDRESS_UUID = sys_uuid.UUID("55814431-ede5-4c4e-abd6-e61600a3069b")
VAR_ECOSYSTEM_ENDPOINT_UUID = sys_uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5")
VAR_DISABLE_TELEMETRY_UUID = sys_uuid.UUID("f6e5d4c3-b2a1-4f9e-8d7c-6b5a4f3e2d1c")
VAR_REALM_UUID_UUID = sys_uuid.UUID("9f8e7d6c-5b4a-4c3d-2e1f-0a9b8c7d6e5f")
VAR_REALM_SECRET_UUID = sys_uuid.UUID("3e2d1c0b-9a8f-4e7d-6c5b-4a3f2e1d0c9b")
VAR_REALM_ACCESS_TOKEN_UUID = sys_uuid.UUID("7c6b5a4f-3e2d-4c1b-0a9f-8e7d6c5b4a3f")
VAR_REALM_REFRESH_TOKEN_UUID = sys_uuid.UUID("eacf0c1f-3495-4986-89a5-80139526b82a")
VAR_HS256_JWKS_ENCRYPTION_KEY_UUID = sys_uuid.UUID(
    "c371a647-e1a6-4bec-bef2-a50041bc5af2"
)

REPOSITORY_URL = "https://repo.exordos.com"
ELEMENTS_PATH = "exordos-elements"
ELEMENT_REPO_URL = f"{REPOSITORY_URL}/{ELEMENTS_PATH}"
INVENTORY_URL = f"{REPOSITORY_URL}/{ELEMENTS_PATH}/inventory.json"
