---
icon: lucide/key-round
---
## 1. Permissions Overview

In the Identity and Access Management (IAM) system, a **Permission** is a core entity that defines the right to perform a specific action on a specific resource. IAM operates on a **"deny by default" model**: if a user or service does not have an explicitly granted permission for an action, that action is blocked.

### 1.1. Permission Structure

Each permission is represented as a string consisting of three parts separated by dots:

`<service_name>.<resource_name>.<action>`

* **`<service_name>`**:
    * A unique identifier for a service or module in your system
    * Examples: `billing`, `compute`, `auth`, `storage`

* **`<resource_name>`**:
    * Defines the type of object an action can be performed on
    * Expressed in the **singular form**
    * Examples: `account`, `vm`, `user`, `policy`

* **`<action>`**:
    * Defines the operation that can be performed on the specified resource
    * Examples: `read`, `write`, `create`, `delete`, `activate`

**Permission examples:**

* `billing.account.read` — view account information in the billing service
* `compute.vm.create` — create a new virtual machine in the compute service
* `auth.user.deactivate` — deactivate a user in the authentication service

You can also use `*` (wildcard) in any of the three parts. It means **all** values are allowed in that part.

**How `*` works by part:**

* `*` in the first part (`service_name`) — access for **all services**
* `*` in the second part (`resource_name`) — access for **all resources** in the selected service
* `*` in the third part (`action`) — access for **all actions** on the selected resource

**Wildcard permission examples:**

* `*.vm.read` — allows reading `vm` in all services
* `compute.*.read` — allows reading any resource in the `compute` service
* `compute.vm.*` — allows any action on `vm` in the `compute` service
* `*.*.*` — full access to all services, resources, and actions

### 1.2. Security Model: "Deny by Default"

An important IAM philosophy is the principle of **explicit permission**:

* Initially, any user or service account has no access rights
* To perform any action, the subject must be granted the corresponding permission
* Services integrated with IAM must check for the required permission before executing an operation

### 1.3. Permissions API Examples

#### Create a Permission

```http
POST /v1/iam/permissions/
Content-Type: application/json
Authorization: Bearer <token>

{
  "name": "compute.vm.create",
  "description": "Permission to create a virtual machine"
}
```

#### Response

```json
{
  "uuid": "5a1b2c3d-4e5f-6789-abcd-ef0123456789",
  "name": "compute.vm.create",
  "description": "Permission to create a virtual machine",
  "created_at": "2025-08-21T07:38:04.778680Z",
  "updated_at": "2025-08-21T07:38:04.778688Z",
  "status": "ACTIVE"
}
```

#### Get a Permission

```http
GET /v1/iam/permissions/5a1b2c3d-4e5f-6789-abcd-ef0123456789
Authorization: Bearer <token>
```

#### Filter Permissions

```http
GET /v1/iam/permissions/?name=compute.vm.create&status=ACTIVE
Authorization: Bearer <token>
```

## 2. Roles

A **Role** is a named collection of Permissions. While a Permission defines the right for one specific action, a Role groups these rights into logical blocks corresponding to job functions, responsibilities, or the access level of a user or service.

### 2.1. Permission Binding: Linking Roles and Permissions

To assign permissions to a role, the **Permission Binding** entity is used. This entity establishes a many-to-many relationship between roles and permissions.

* **One Permission** can be bound to **several different roles**
* **One Role** can contain **many different permissions**

**Example:**

* The `BillingViewer` role receives the following via Permission Binding:
    * `billing.account.read`
    * `billing.invoice.read`
* The `BillingOperator` role receives the following via Permission Binding:
    * `billing.account.read`
    * `billing.invoice.read`
    * `billing.invoice.pay`

### 2.2. Role Binding: Assigning Roles to Users

To grant a user access, a role must be assigned to them. The **Role Binding** entity is used for this purpose. This entity establishes a many-to-many relationship between users and roles.

* **One user** can be assigned **several roles**
* **One role** can be assigned to **many users**

### 2.3. Final Access Model

The access verification process:

1. A **User** calls a service with a token
2. The **Service** calls IAM token introspection
3. The service gets the list of permissions available for this token from the introspection response
4. The **Service** checks whether the required permission exists for the requested action
5. If the permission is present, access is **granted**

### 2.4. Roles API Examples

#### Create a Role

```http
POST /v1/iam/roles/
Content-Type: application/json
Authorization: Bearer <token>

{
  "name": "BillingOperator",
  "description": "Billing operator with rights to manage accounts"
}
```

#### Response

```json
{
  "uuid": "6b2c3d4e-5f67-789a-bcde-f01234567890",
  "name": "BillingOperator",
  "description": "Billing operator with rights to manage accounts",
  "created_at": "2025-08-21T07:38:04.779416Z",
  "updated_at": "2025-08-21T07:38:04.779424Z",
  "status": "ACTIVE",
  "project_id": null
}
```

#### Create a Permission Binding

```http
POST /v1/iam/permission_bindings/
Content-Type: application/json
Authorization: Bearer <token>

{
  "role": "6b2c3d4e-5f67-789a-bcde-f01234567890",
  "permission": "5a1b2c3d-4e5f-6789-abcd-ef0123456789"
}
```

#### Create a Role Binding

```http
POST /v1/iam/role_bindings/
Content-Type: application/json
Authorization: Bearer <token>

{
  "user": "7c3d4e5f-6789-89ab-cdef-123456789012",
  "role": "6b2c3d4e-5f67-789a-bcde-f01234567890",
  "project": "8d4e5f67-789a-9abc-def1-234567890123"
}
```

#### Get User Roles

```http
GET /v1/iam/users/7c3d4e5f-6789-89ab-cdef-123456789012/actions/get_my_roles
Authorization: Bearer <token>
```

### 2.5. Projects and Role/Permission Scope

In IAM, roles can be assigned as:

* **Global** (without project, `project = null`)
* **Project-scoped** (via the `project` field in `Role Binding`)

This directly affects the effective permission set in a token.

When issuing a token, IAM determines the project from `scope`:

* `project:<uuid>` — token is bound to the specified project
* `project:default` — token is bound to the user's default project
* if no `project:...` segment is present in `scope`, the token is issued **without project** (`project = null`)

During introspection, IAM returns permissions only in the context of the token's project.
In other words, the effective permission set depends on which project the token is issued for.

Important:
getting a token **without project** does not automatically grant access to all projects.
Such token includes only permissions valid in `project = null` context (global assignments) and does not include project-scoped assignments from other projects.

### 2.6. How Services Should Check Permissions (Practical Template)

Below is a recommended integration model for a business service with IAM.

#### Basic flow

1. Service receives a user token (`Authorization: Bearer ...`)
2. Service calls IAM token introspection endpoint
3. Service gets the permission list from introspection for the current token
4. Before each protected action, service checks that required permission is present
5. If permission is missing, service returns `403 Forbidden`

#### Minimal service code structure

```python
class IamClient:
    def introspect(self, token: str) -> dict:
        # HTTP GET /v1/iam/clients/<client_uuid>/actions/introspect/invoke
        # with Authorization: Bearer <token>
        ...


class AuthContext:
    def __init__(self, token: str, introspection: dict):
        self.token = token
        self.introspection = introspection
        self.permissions = {
            p["name"] if isinstance(p, dict) else str(p)
            for p in introspection.get("permissions", [])
        }

    def has_permission(self, permission_name: str) -> bool:
        return permission_name in self.permissions


class PermissionDenied(Exception):
    pass
```

#### Guard/decorator for permission checks

```python
def require_permission(permission_name: str):
    def wrapper(handler):
        def inner(request, *args, **kwargs):
            ctx: AuthContext = request.auth_context
            if not ctx.has_permission(permission_name):
                raise PermissionDenied(
                    f"Missing required permission: {permission_name}"
                )
            return handler(request, *args, **kwargs)
        return inner
    return wrapper
```

#### Endpoint example

```python
@require_permission("compute.vm.create")
def create_vm(request):
    payload = request.json
    # VM creation business logic
    return {"status": "ok"}, 201
```

#### Context initialization in middleware

```python
def auth_middleware(request, iam_client: IamClient):
    token = extract_bearer_token(request.headers)
    if not token:
        return {"error": "Unauthorized"}, 401

    introspection = iam_client.introspect(token)
    request.auth_context = AuthContext(token=token, introspection=introspection)
    return None  # continue
```

#### Practical recommendations

* Check permissions as close as possible to action execution point (endpoint/use-case)
* Do not infer rights in the service — IAM must stay the source of truth
* Only short-lived server-side cache for introspection response is acceptable; do not cache permissions on the client side
* Log access denials with required permission and user/token context

## 3. Best Practices

### 3.1. Principle of Least Privilege

Create roles that provide exactly the level of access required to perform a task, and nothing more.

### 3.2. Semantic Naming

Give roles and permissions clear names that reflect their purpose:

* Roles: `NetworkReadOnly`, `DatabaseSuperUser`
* Permissions: `compute.vm.read`, `storage.bucket.delete`

### 3.3. Regular Audits

Periodically review:

* Which roles are assigned to whom
* Which permissions are included in roles
* Remove unnecessary access promptly

### 3.4. Using Projects for Isolation

Use project-scoped Role Binding to isolate access between environments.
Detailed project-context behavior is described in section **2.5**.

## 4. Error Handling

The following errors may occur when working with the IAM API:

### 4.1. Access Error (403 Forbidden)

```json
{
  "status": 403,
  "json": {
    "code": 403,
    "type": "PermissionDeniedException",
    "message": "User does not have required permission: compute.vm.create"
  }
}
```

### 4.2. Not Found (404 Not Found)

```json
{
  "status": 404,
  "json": {
    "code": 404,
    "type": "NotFoundException",
    "message": "Role with uuid 6b2c3d4e-5f67-789a-bcde-f01234567890 not found"
  }
}
```

### 4.3. Bad Request (400 Bad Request)

```json
{
  "status": 400,
  "json": {
    "code": 400,
    "type": "ValidationErrorException",
    "message": "Field 'name' must be between 0 and 255 characters"
  }
}
```

## 5. Important Notes

1. All changes to permissions and role bindings take effect immediately
2. Do not cache permissions on the client side; if caching is needed, use only short-lived server-side introspection cache
3. For service accounts, use separate roles with the minimum required permissions
4. Regularly update and review role assignments within the system
