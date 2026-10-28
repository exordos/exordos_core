# Подключение on-premises к OpenStack

Процедура добавляет OpenStack к существующему Core и соединяет приватные сети.
On-premises шлюз инициирует OpenVPN-соединение и может находиться за NAT.
Облачный шлюз принимает соединение на floating IP. Балансировщики в облаке
обращаются к приватным backend-адресам on-premises через этот туннель.

## Подготовка

- На Core и узлах нужны совместимые Core, SDK и SeedOS с cloud-драйверами
  и `OpenVPNCapabilityDriver`. На Core и шлюзах нужен OpenVPN 2.6+.
- Получите проектный application credential OpenStack либо имя пользователя,
  пароль, домен и проект. Нужны права на Nova, Neutron, Cinder и Glance.
  Учётные данные передаются в API и остаются только на Core.
- Core должен достигать endpoint из Keystone catalog независимо от link.
  Шлюзам при bootstrap нужен доступ к репозиториям пакетов.
- Выберите непересекающиеся CIDR. В примере облачная подсеть — `10.80.0.0/24`;
  замените её, если она пересекается с подключаемыми сетями.
- Для управляемого on-premises шлюза нужны существующая приватная подсеть
  `ovs_evpn` и транспортная `flat_bridge` подсеть с исходящим доступом к
  облачному шлюзу по TCP/443. Транспортная подсеть принадлежит тому же проекту
  Core, имеет default gateway, разрешает размещение и не имеет `next_server`.
  Её адрес может быть приватным.
- Подготовьте образ местного шлюза с guest/render/service-агентами Core и
  Ubuntu cloud-init образ в Glance для облачного шлюза. В Nova должны быть
  flavor под шлюзы (по умолчанию 1 vCPU, 1024 MiB RAM) и под рабочие ВМ.

`PROJECT_ID` — UUID проекта Core, а не проекта OpenStack.
`ONPREM_SUBNET_ID` и `ONPREM_TRANSPORT_SUBNET_ID` — UUID существующих подсетей
Core. `OS_EXTERNAL_NETWORK_ID` и `OS_GATEWAY_IMAGE_ID` — UUID провайдера.

Все запросы ниже идут в аутентифицированный пользовательский API Core.
Для учётных записей облака и link нужны права compute hypervisor.
Замените параметры в угловых скобках и примерные URL реальными значениями.
Сохраняйте `uuid` из ответов. Например, тело первого запроса запишите
в `connection.json` и отправьте:

```sh
curl -fsS "$CORE/v1/compute/cloud_connections/" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data-binary @connection.json
```

## Учётная запись облака

`POST /v1/compute/cloud_connections/`; сохраните `CONNECTION_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "openstack",
    "auth_url": "https://identity.example/v3",
    "auth_type": "v3password",
    "auth": {
      "username": "<OS_USERNAME>",
      "password": "<OS_PASSWORD>",
      "user_domain_name": "<OS_USER_DOMAIN_NAME>",
      "project_id": "<OS_PROJECT_ID>"
    },
    "region": "RegionOne"
  }
}
```

`driver_spec.kind` выбирает схему драйвера. Тип `openstack` содержит параметры
Keystone и каталога сервисов. Core сохраняет проверенный проект OpenStack
в доступном только для чтения `driver_state.project_id`.
Объект `driver_spec.auth` доступен только для записи: ответы создания, просмотра, списка
и обновления его не содержат. Файлы конфигурации провайдера и переменные
окружения на Core не требуются.

Та же регистрация через CLI. Если `OS_PASSWORD` не задан в окружении CLI,
пароль запрашивается без отображения:

```sh
exordos compute cloud-connections add --name remote-openstack --kind openstack \
  --project-id "$PROJECT_ID" --auth-url https://identity.example/v3 \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name "$OS_USER_DOMAIN_NAME" \
  --region RegionOne
```

`--project-id` обозначает проект Core; `driver_spec.auth.project_id` и `--os-project-id` —
проект OpenStack. Имя пользователя, его домен, UUID проекта и адрес Keystone
возьмите из параметров доступа по API у провайдера. Для выбора проекта по имени
вместо `driver_spec.auth.project_id` укажите `project_name` и `project_domain_name`.

Для замены пароля отправьте `PUT` с полным объектом `driver_spec`, включая
`kind` и обновлённый `auth`. CLI сам читает существующие открытые параметры:

```sh
exordos compute cloud-connections update-credentials "$CONNECTION_ID" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name "$OS_USER_DOMAIN_NAME"
```

Core проверяет новые данные и отклоняет подмену проекта провайдера. Адрес
Keystone, тип аутентификации, регион и интерфейс неизменяемы; для другой области
облака создайте другое подключение.

Application credentials — альтернативный способ, если провайдер их поддерживает:
задайте `driver_spec.auth_type: "v3applicationcredential"` и поля `application_credential_id`
и `application_credential_secret` в `driver_spec.auth`. CLI принимает
`--application-credential-id` и запрашивает соответствующий секрет.

Core берёт адреса Orchestrator/Status из общего Boot API своей инсталляции
и выбирает установленную версию SDK для bootstrap. Эти параметры не задаются
при добавлении облака. Адрес сервиса должен быть доступен из подключённой сети;
для management-сети нужны её подключение и обратный маршрут. Адрес сервиса
должен быть отделён от хоста link-шлюза: новые соединения из туннеля к его
собственным службам блокируются. Местные агенты должны достигать Core независимо
от облачного link. При bootstrap нужны доступ к репозиторию пакетов и
зависимостям SDK.

## Облачная сеть и вычислительный пул

`POST /v1/network/networks/`; сохраните `CLOUD_NETWORK_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-private",
  "driver_spec": {
    "driver": "cloud",
    "connection": "<CONNECTION_ID>",
    "external_network": "<OS_EXTERNAL_NETWORK_ID>"
  }
}
```

`POST /v1/network/subnets/`; сохраните `CLOUD_SUBNET_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-private",
  "network": "<CLOUD_NETWORK_ID>",
  "cidr": "10.80.0.0/24",
  "dns_servers": ["1.1.1.1"],
  "routers": [{"to": "0.0.0.0/0", "via": "10.80.0.1"}]
}
```

Укажите DNS-серверы своей инсталляции. Core создаёт принадлежащую ему Neutron
сеть и маршрутизатор во внешнюю сеть. Ранее созданные немаркированные сети
и ВМ автоматически не подхватываются.

`POST /v1/compute/hypervisors/` регистрирует облачный пул:

```json
{
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "cloud",
    "connection": "<CONNECTION_ID>",
    "volume_type": "__DEFAULT__"
  }
}
```

Один пул использует общие квоты этой учётной записи. При необходимости SSH
через готовый OpenStack keypair укажите его имя в `driver_spec.key_name`.

## Приватная связность

`POST /v1/compute/cloud_network_links/`; сохраните `LINK_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "onprem-cloud",
  "transport": {"driver": "openvpn", "protocol": "tcp", "port": 443},
  "endpoints": [
    {
      "subnet": "<ONPREM_SUBNET_ID>",
      "role": "client",
      "external_subnet": "<ONPREM_TRANSPORT_SUBNET_ID>",
      "gateway_image": "https://images.example/onprem-gateway.raw.zst"
    },
    {
      "subnet": "<CLOUD_SUBNET_ID>",
      "role": "server",
      "external_network": "<OS_EXTERNAL_NETWORK_ID>",
      "gateway_image": "<OS_GATEWAY_IMAGE_ID>"
    }
  ],
  "mtu": 1380
}
```

Core создаёт оба шлюза и маршруты. Местный шлюз устанавливает соединение
с floating IP облачного. Входящий проброс порта на on-premises не требуется.
Облачный транспорт стартует до появления доступа из гостя к Core.
Проверяйте `GET /v1/compute/cloud_network_links/<LINK_ID>` до `ACTIVE`, затем
проверьте трафик рабочих ВМ в обе стороны. Существующим ВМ требуется
обновление DHCP-аренды для получения маршрутов.

Для готового местного маршрутизирующего шлюза замените его endpoint на:

```json
{"subnet": "<ONPREM_SUBNET_ID>", "agent": "<GATEWAY_AGENT_ID>", "role": "client"}
```

SDK-агент должен объявлять `cloud_net_link`. Местные маршруты и подключение
к SDN обеспечивает оператор; конфигурацию VPN доставляет Core.

## Использование облака

Рабочие ВМ создаются обычным API Core. Пример облачного узла под балансировщик:
`POST /v1/compute/nodes/`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-lb-1",
  "cores": 2,
  "ram": 2048,
  "default_network": {"subnet": "<CLOUD_SUBNET_ID>"},
  "disk_spec": {
    "kind": "root_disk",
    "image": "<compatible Glance image ID or allowed image URL>",
    "size": 20,
    "ephemeral": false
  }
}
```

Планировщик выбирает облачный пул по учётной записи подсети. Core создаёт ВМ
в Nova и постоянные диски в Cinder. Для NodeSet укажите ту же подсеть в
`default_network.subnet`. В балансировщике задайте приватные IP backend-узлов
on-premises. Для публичного frontend настройте вход в OpenStack, например
floating IP на порту ВМ балансировщика. Создание Node само по себе его не
публикует. Floating IP link-шлюза обслуживает VPN и не публикует балансировщик.
Security groups должны разрешать frontend/backend трафик и ответы.
Исходные приватные адреса сохраняются. Для дополнительных постоянных дисков
задавайте `ephemeral: false`. URL образов должны входить в
`[openstack_cloud] image_origins`.

Наблюдайте Nodes, Ports и Volumes обычным API Core. `ACTIVE` у link означает
готовность транспорта; приложение проверяйте отдельно. Для добавления площадок
отправьте `PUT` с полным списком endpoint. CIDR не должны пересекаться;
два явных клиента или два явных сервера не образуют пару full mesh.

Для отключения удалите рабочие ресурсы и link, дождитесь завершения его
очистки, затем удалите неиспользуемые пулы, подсети, сети и учётную запись.
