# Подключение on-premises к OpenStack

Одна логическая сеть объединяет существующую on-premises подсеть и новые
облачные подсети. Местный клиент за NAT подключается к floating IP облачного
сервера. Балансировщики в облаке достигают приватных backend через этот туннель.

## Подготовка

Установите совместимые Core, SDK и SeedOS с OpenStack extra и
`OpenVPNCapabilityDriver`. На Core и шлюзах нужен OpenVPN 2.6+.
Core должен достигать Keystone catalog независимо от приватного link;
bootstrap требует доступа к репозиториям пакетов. Файл конфигурации провайдера
на Core не нужен.

Получите URL Keystone, имя пользователя, пароль, домен и проект с правами
Nova, Neutron, Glance и Cinder. Подготовьте Ubuntu cloud-init образ шлюза
в Glance и подходящие flavor Nova (шлюз по умолчанию: 1 vCPU, 1024 MiB RAM).
Местному образу нужны guest/render/service-агенты Core.
Нужны существующая приватная OVS/EVPN подсеть и транспортная `flat_bridge`
подсеть того же проекта Core, разрешающая размещение, с default gateway
и без `next_server`. Она должна достигать облачного шлюза по TCP/443
и может иметь приватный адрес.

Все запросы идут в аутентифицированный пользовательский API Core. Для операций
с инфраструктурой нужны соответствующие права compute hypervisor.
UUID проектов Core и OpenStack различаются. Подставьте реальные значения
и сохраняйте UUID из ответов. Учётные данные не должны попадать в репозиторий.

## Регистрация облачного гипервизора

`POST /v1/compute/hypervisors/`; сохраните `CLOUD_HYPERVISOR_ID`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "openstack",
    "auth_url": "https://identity.example/v3",
    "auth_type": "v3password",
    "auth": {
      "username": "<OS_USERNAME>",
      "password": "<OS_PASSWORD>",
      "user_domain_name": "Default",
      "project_id": "<OS_PROJECT_ID>"
    },
    "region": "RegionOne",
    "volume_type": "__DEFAULT__"
  },
  "network_defaults": {
    "external_network": "<OS_EXTERNAL_NETWORK_ID>",
    "gateway_image": "<OS_GATEWAY_IMAGE_ID>",
    "role": "server"
  }
}
```

Гипервизор представляет проект и регион провайдера. Core проверяет доступ
и записывает идентичность провайдера в read-only `driver_state`.
`driver_spec.auth` доступен только для записи, исключён из ответов и целей агентов.
`network_defaults` задаёт шлюзы по умолчанию для подсетей этого облака.
Для готового keypair OpenStack можно указать `driver_spec.key_name`.

Регистрация и замена учётных данных через CLI:

```sh
exordos compute hypervisors add --name remote-openstack --kind openstack \
  --project-id "$PROJECT_ID" --auth-url https://identity.example/v3 \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default \
  --region RegionOne --network-defaults-file cloud-network-defaults.json
exordos compute hypervisors update-credentials "$CLOUD_HYPERVISOR_ID" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default
```

Файл настроек содержит объект `network_defaults` из примера.
Если `OS_PASSWORD` не задан, CLI запрашивает пароль без отображения.
`--project-id` — проект Core, `--os-project-id` — проект OpenStack.
Для выбора проекта провайдера по имени используйте `project_name` и
`project_domain_name` в API auth либо соответствующие параметры CLI.

Для замены через API отправьте `PUT` с полным `driver_spec`, включая новый
`auth`. Пароль и application credential можно менять друг на друга при
сохранении идентичности провайдера. URL Keystone, регион и интерфейс неизменяемы.
Если провайдер поддерживает application credentials, используйте
`auth_type: "v3applicationcredential"` и `application_credential_id` вместе
с `application_credential_secret` внутри `auth`.

Несекретные настройки драйвера меняются без повторной передачи credentials:

```sh
exordos compute hypervisors update "$CLOUD_HYPERVISOR_ID" --driver-spec volume_type=fast
```

Частичное изменение OpenStack принимает `volume_type` и `key_name`, отдельно
от изменения имени/site. Запрос `POST /v1/compute/hypervisors/<uuid>/actions/update_driver_settings/invoke`
с `{"settings": {"volume_type": "fast"}}` сохраняет credentials на Core.
Конечные квоты сохраняют значения провайдера. Безлимитные квоты (`-1`)
представлены верхней границей поддерживаемого счётчика; доступная квота
не означает физическую ёмкость провайдера.

Адреса Orchestrator/Status используют хост из Boot API инсталляции
и порты `orch_api.bind_port` и `status_api.bind_port` из её конфигурации.
Оба порта HTTP API должны быть опубликованы на этом хосте.
Протокол агента шифрует запросы и ответы ключом узла.
Агентам нужны доступный адрес сервиса и обратный маршрут. Адрес сервиса должен
быть отделён от служб самого link-шлюза: новые соединения из туннеля к ним
блокируются. Доступ к провайдеру и доступ местных агентов к Core должны
сохраняться при потере облачного link.

## Создание логической сети

`POST /v1/network/networks/`; сохраните `NETWORK_ID`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "application",
  "cidr": "10.80.0.0/16"
}
```

CIDR — пул для новых подсетей `/23`. Диапазоны всех участников должны
не пересекаться, включая существующую on-premises подсеть.

`PUT /v1/network/subnets/<ONPREM_SUBNET_ID>`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<ONPREM_HYPERVISOR_ID>"
  ],
  "gateway": {
    "role": "client",
    "external_subnet": "<ONPREM_TRANSPORT_SUBNET_ID>",
    "gateway_image": "https://images.example/onprem-gateway.raw.zst"
  }
}
```

Существующие подсеть и нагрузки сохраняют адреса. Перечислите все совместимые
on-premises гипервизоры, на которых могут работать её ВМ. Параметры `gateway`
можно вместо этого задать в их `network_defaults`.

`POST /v1/network/subnets/`; сохраните `CLOUD_SUBNET_ID`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<CLOUD_HYPERVISOR_ID>"
  ],
  "dns_servers": [
    "1.1.1.1"
  ]
}
```

Core выделяет `/23`, создаёт принадлежащие ему ресурсы Neutron и управляет link.
Местный шлюз инициирует соединение; проброс входящих портов через NAT не нужен.
Отдельный запрос CloudNetworkLink не требуется. Дождитесь ACTIVE у участников
через API подсетей, затем проверьте трафик приложения в обе стороны.
Существующим гостям нужно обновить DHCP для получения маршрутов.

## Использование

Для создания балансировщика в логической сети отправьте
`POST /v1/compute/nodes/`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "cloud-lb-1",
  "cores": 2,
  "ram": 2048,
  "default_network": {
    "network": "<NETWORK_ID>"
  },
  "disk_spec": {
    "kind": "root_disk",
    "image": "<GLANCE_IMAGE_ID_OR_ALLOWED_URL>",
    "size": 20,
    "ephemeral": false
  }
}
```

NodeSet для распределения по участвующим гипервизорам выбирает логическую сеть:

```json
{
  "default_network": {
    "network": "<NETWORK_ID>"
  },
  "placement": {
    "kind": "anti-affinity",
    "scope": "site"
  }
}
```

Планировщик выбирает подходящие подсеть и гипервизор каждой реплике.
Необязательная метка `site` гипервизора объединяет хосты в домен отказа;
без неё доменом служит UUID гипервизора. См. [Логические сети](logical-networks.ru.md).

В балансировщике укажите приватные on-premises адреса backend.
Публичный frontend настраивается отдельно; floating IP VPN-шлюза нагрузку
не публикует. Security groups должны разрешать frontend/backend трафик и ответы.
Туннель сохраняет приватные исходные адреса. Постоянным дискам нужен
`ephemeral: false`; URL образов должны соответствовать
`[openstack_cloud] image_origins`. Flavor провайдера должен подходить
к запрошенным vCPU/RAM каждой ВМ.

Добавляйте новые подсети через тот же API. Новые подсети одного облачного
гипервизора при `auto` используют общий маршрутизатор Neutron; другие пары —
прямой OpenVPN. Транзит и миграция нагрузки в этот контракт связности не входят.

Перед удалением подсети или сети удалите нагрузки. Очистка асинхронна.
Импортированные on-premises подсети сохраняются при отключении.
Облачный гипервизор удаляется после всех зависимых ресурсов и записей очистки.

## Ручная настройка через CLI

Последовательность подключает существующую on-premises подсеть и одно облако
OpenStack к общей сети. В CLI должна быть выбрана нужная инсталляция Core
и учётная запись с правами управления инфраструктурой. Core, агенты и образы
должны содержать совместимые OpenStack/SDN-драйверы, описанные выше.
Команды настраивают существующую инсталляцию; сборка образов описана
в [Build](../app-developer-guide/build.ru.md).

Подставьте значения параметров. UUID проекта Core отличается от проекта
провайдера. Для нагрузки нужен URL образа, пригодного и для местных гипервизоров,
и для OpenStack: Glance UUID не позволяет загрузить on-premises ВМ.
Flavor Nova должны соответствовать ресурсам шлюза и нагрузки. Для местных ВМ
с `--durable` также нужен местный storage pool с `ephemeral: false`.
Сохраните сгенерированные UUID для просмотра и удаления ресурсов.

```sh
PROJECT_ID='<CORE_PROJECT_UUID>'
ONPREM_HYPERVISOR_ID='<EXISTING_ONPREM_HYPERVISOR_UUID>'
ONPREM_SUBNET_ID='<EXISTING_PRIVATE_SUBNET_UUID>'
ONPREM_TRANSPORT_SUBNET_ID='<EXISTING_FLAT_TRANSPORT_SUBNET_UUID>'

OS_AUTH_URL='https://identity.example/v3'
OS_USERNAME='<OPENSTACK_USERNAME>'
OS_PROJECT_ID='<OPENSTACK_PROJECT_ID>'
OS_REGION='RegionOne'
OS_EXTERNAL_NETWORK_ID='<NEUTRON_EXTERNAL_NETWORK_UUID>'
OS_GATEWAY_IMAGE_ID='<GLANCE_GATEWAY_IMAGE_UUID>'
ONPREM_GATEWAY_IMAGE_URL='https://images.example/gateway.raw.zst'
WORKLOAD_IMAGE_URL='https://repo.exordos.com/<compatible-workload-image>'

new_uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }
CLOUD_HYPERVISOR_ID=$(new_uuid)
NETWORK_ID=$(new_uuid)
CLOUD_SUBNET_ID=$(new_uuid)
NODESET_ID=$(new_uuid)
WORK_DIR=$(mktemp -d)

exordos compute hypervisors list
exordos network subnets show "$ONPREM_SUBNET_ID"
```

Регистрируем облако. Файл defaults не содержит учётных данных; пароль
запрашивается без отображения, если не задан через `OS_PASSWORD`.

```sh
cat > "$WORK_DIR/cloud-network-defaults.json" <<EOF
{
  "external_network": "$OS_EXTERNAL_NETWORK_ID",
  "gateway_image": "$OS_GATEWAY_IMAGE_ID",
  "role": "server"
}
EOF

exordos compute hypervisors add \
  --uuid "$CLOUD_HYPERVISOR_ID" --name openstack-a \
  --project-id "$PROJECT_ID" --site openstack-a \
  --kind openstack --auth-url "$OS_AUTH_URL" --region "$OS_REGION" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default \
  --network-defaults-file "$WORK_DIR/cloud-network-defaults.json"
```

Создаём сеть, подключаем существующую подсеть и выделяем облачный `/23`.
On-premises CIDR сохраняется. Повторите `--hypervisor`, если местная подсеть
доступна на нескольких совместимых хостах одной фабрики.

```sh
exordos network networks add \
  --uuid "$NETWORK_ID" --name application --project-id "$PROJECT_ID" \
  --cidr 10.80.0.0/16 --connectivity auto

cat > "$WORK_DIR/onprem-gateway.json" <<EOF
{
  "role": "client",
  "external_subnet": "$ONPREM_TRANSPORT_SUBNET_ID",
  "gateway_image": "$ONPREM_GATEWAY_IMAGE_URL"
}
EOF

exordos network subnets update "$ONPREM_SUBNET_ID" \
  --network "$NETWORK_ID" --hypervisor "$ONPREM_HYPERVISOR_ID" \
  --gateway-file "$WORK_DIR/onprem-gateway.json"

exordos network subnets add \
  --uuid "$CLOUD_SUBNET_ID" --name application-openstack-a \
  --project-id "$PROJECT_ID" --network "$NETWORK_ID" \
  --hypervisor "$CLOUD_HYPERVISOR_ID" --dns-server 1.1.1.1
```

Core создаёт link и оба шлюза. Облачный сервер принимает соединение
на floating IP; местный клиент может находиться за NAT.
Отдельно создавать link или настраивать VPN не требуется.

```sh
exordos compute hypervisors show "$CLOUD_HYPERVISOR_ID" --output json
exordos network networks show "$NETWORK_ID" --output json
exordos network subnets show "$ONPREM_SUBNET_ID" --output json
exordos network subnets show "$CLOUD_SUBNET_ID" --output json
exordos network subnets list --filters "network=$NETWORK_ID"
```

Дождитесь ACTIVE у облачного гипервизора и обеих подсетей. Ответы подсетей
в JSON содержат `status` и `error`, наблюдения сети — `driver_state.subnets`.
Устраните ошибки до создания нагрузки. Существующим гостям нужно обновить
DHCP для получения маршрутов. Адрес сервиса Core требует собственного
подключения и обратного маршрута к присоединённой подсети.

При необходимости запускаем две реплики, по одной на домен отказа:

```sh
exordos compute sets add \
  --uuid "$NODESET_ID" --name multicloud-smoke --project-id "$PROJECT_ID" \
  --network "$NETWORK_ID" --image "$WORKLOAD_IMAGE_URL" \
  --cores 1 --ram 1024 --root-disk 10 --durable --replicas 2 \
  --placement-kind anti-affinity --placement-scope site

exordos compute sets show "$NODESET_ID" --output json
exordos compute nodes list --filters "node_set=$NODESET_ID"
exordos network ports list
```

Для корневых дисков Cinder нужен `--durable`. На обоих доменах должны быть
вычислительные мощности и постоянное хранилище. При строгой anti-affinity
и двух доменах третья реплика останется в ожидании. Необязательная метка
`site` гипервизора объединяет физические хосты в один домен; без неё доменом
служит UUID гипервизора.

IP реплик найдите в Ports и проверьте ICMP либо TCP-порт приложения в обе стороны
из самих ВМ. Security groups должны разрешать проверяемый трафик. ACTIVE подсети
означает готовность связности, но не подтверждает готовность приложения
и не публикует публичный frontend.
