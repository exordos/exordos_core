# Логические сети и подсети

Network объединяет приватные подсети разных гипервизоров и облаков. CIDR сети —
пул адресов. Subnet выбирает гипервизоры по UUID; учётные данные облака и настройки
шлюза по умолчанию находятся в гипервизорах. NodeSet выбирает Network.

## Создание сети и подсети

`POST /v1/network/networks/`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "application",
  "cidr": "10.80.0.0/16",
  "connectivity": {
    "driver": "auto",
    "protocol": "tcp",
    "port": 443,
    "mtu": 1380
  }
}
```

Пул IPv4 должен быть размером не меньше `/23`. Связность по умолчанию: `auto`,
TCP/443, MTU 1380. CIDR сети, проект и политика связности неизменяемы.

`POST /v1/network/subnets/`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<HYPERVISOR_ID>"
  ],
  "dns_servers": [
    "1.1.1.1"
  ]
}
```

Core выделяет свободный `/23` и default route через первый доступный адрес.
Явный CIDR должен быть свободным `/23` внутри пула. DNS задаётся на подсети.
Диапазон сохраняется при изменениях метаданных и освобождается после очистки.

Облачная подсеть указывает ровно один гипервизор `kind: "openstack"` в проекте
Core этой сети. Местная SDN-подсеть может указывать несколько совместимых
гипервизоров `libvirt` или `exordos_local_hyper` с включённым OVS в одной фабрике.
ВМ могут размещаться на любом выбранном хосте. Гипервизор OpenStack описывает
проект и регион провайдера, а не отдельный хост Nova.

## Подключение существующей подсети

`PUT /v1/network/subnets/<SUBNET_ID>`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<ONPREM_HOST_A>",
    "<ONPREM_HOST_B>"
  ],
  "gateway": {
    "role": "client",
    "external_subnet": "<TRANSPORT_SUBNET_ID>",
    "gateway_image": "https://images.example/gateway.raw.zst"
  }
}
```

CIDR, адреса и нагрузки сохраняются. Импортируемый CIDR может находиться вне пула,
но не должен пересекаться с другими участниками. Драйвер исходной сети должен
соответствовать выбранным гипервизорам. Удаление импортированного участника
из общей сети сохраняет исходные подсеть и сеть.

## Связность

`auto` соединяет новые подсети одного облачного гипервизора общим маршрутизатором
Neutron; их внешняя сеть должна совпадать. Другие пары, включая импортированные
подсети, используют принадлежащий сети CloudNetworkLink с управляемыми шлюзами
OpenVPN. `connectivity.driver: "openvpn"` выбирает OpenVPN для всех пар.

Настройте `Hypervisor.network_defaults` до добавления подсетей либо переопределите
значения через `Subnet.gateway`. Для облака доступны `external_network`,
`gateway_image`, `role`, `gateway_cores` и `gateway_ram`. Для местных
гипервизоров — `external_subnet`, `gateway_image`, `gateway_disk`, те же
параметры роли и ресурсов, либо готовый `agent` с необязательным доступным
`endpoint`. Требования к шлюзам описаны в
[OpenStack multicloud](openstack-multicloud.md).

Итоговые настройки должны совпадать у хостов одной местной подсети; явное
переопределение на подсети разрешает различия. Значения фиксируются при подключении.
Размещение, CIDR и параметры сети провайдера неизменяемы. Настройки шлюза можно
менять до привязки endpoint при сохранении параметров сети провайдера.

Роли: `auto`, `server`, `client`. On-premises за NAT может инициировать
соединения к floating IP шлюза OpenStack. Каждой паре нужна принимающая сторона:
два явных клиента или два явных сервера отклоняются. Связи образуют прямой full
mesh без транзита и L2 extension. Автоматическим link владеет Core; состав
участников меняется через подсети.

Управление сетью и её подсетями дополнительно требует соответствующего права
`compute.hypervisor`, поскольку создаёт инфраструктуру. Выделение ресурсов
нагрузке следует обычным правилам проекта и публичных сетей.

## Размещение нагрузки

NodeSet выбирает логическую сеть:

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

Планировщик совместно выбирает подсеть ACTIVE и разрешённый гипервизор, затем
создаёт основной порт. `site` гипервизора — необязательная метка домена отказа;
без неё доменом служит UUID гипервизора. Несколько физических гипервизоров могут
иметь общий site. Подсеть эту метку не дублирует.

`placement.kind`: `anti-affinity` требует разделения, `soft-anti-affinity`
предпочитает разделение; `scope`: `site` или `pool`. Для NodeSet в общей сети
по умолчанию используется soft anti-affinity по site. Строгая политика оставляет
реплики в ожидании, если домены закончились. Добавление мощностей не мигрирует
существующие узлы.

Для `disk_spec.kind: "disks"` незаданные `ephemeral` получают `false`,
если сеть содержит облачные подсети. Явные политики дисков сохраняются.
Доступ контролируют security groups; remote-group правила между подсетями
используют наблюдаемые IP участников. Явные IP нагрузки и автоматическое
выделение floating IP при размещении через логическую сеть недоступны.
Публичные точки входа настраиваются отдельно.

## Наблюдение и удаление

`GET /v1/network/subnets/?network=<NETWORK_ID>` возвращает участников с логической
`network`, `hypervisors`, выделенным `cidr`, `status` и `error`.
`Network.driver_state.subnets` содержит наблюдения по UUID подсетей.

`DELETE /v1/network/subnets/<SUBNET_ID>` удаляет участника. Очистка асинхронна;
при сбое запись остаётся DELETING для повторной попытки. Нагрузки, пользовательские
резервации адресов и чужие link блокируют удаление. Удаление сети использует ту же
очистку и запрещено, пока на сеть ссылаются Nodes или NodeSets. Служебными ресурсами
управляйте через логическую сеть и её подсети.

## CLI

```sh
exordos network networks add --name application --project-id "$PROJECT_ID" \
  --cidr 10.80.0.0/16 --connectivity auto
exordos network subnets add --name application-cloud --project-id "$PROJECT_ID" \
  --network "$NETWORK_ID" --hypervisor "$CLOUD_ID"
exordos network subnets update "$ONPREM_SUBNET_ID" --network "$NETWORK_ID" \
  --hypervisor "$HOST_A" --hypervisor "$HOST_B" --gateway-file gateway.json
exordos compute sets add --project-id "$PROJECT_ID" --image "$IMAGE" \
  --network "$NETWORK_ID" --durable --replicas 2 \
  --placement-kind anti-affinity --placement-scope site
```
