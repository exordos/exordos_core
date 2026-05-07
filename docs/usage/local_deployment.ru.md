# Локальное развёртывание

Данное руководство описывает, как развернуть локальную инсталляцию платформы Exordos на одной хост-машине.

## Зависимости

Предполагается, что на вашей машине используется Linux (Ubuntu).

### Exordos CLI

Установите Exordos CLI:

```bash
curl -fsSL https://repository.genesis-core.tech/install.sh | sudo sh
```

### Пакеты

Установите необходимые пакеты:

```bash
sudo apt update
sudo apt install qemu-kvm qemu-utils libvirt-daemon-system libvirt-dev mkisofs -y
```

Добавьте текущего пользователя в необходимые группы:

```bash
sudo adduser $USER libvirt
sudo adduser $USER kvm
```

## Локальная машина как гипервизор

Локальная машина должна быть настроена как гипервизор, чтобы платформа могла планировать и запускать на ней виртуальные машины.

Инициализируйте текущую машину как гипервизор:

```bash
exordos compute hypervisors init
```

### Основные параметры

Выполните `exordos compute hypervisors init --help`, чтобы просмотреть все доступные параметры. Наиболее важные из них:

| Параметр | Описание |
|---|---|
| `--pool_name TEXT` | Имя пула хранилища libvirt для образов дисков виртуальных машин. По умолчанию: `default`. |
| `--packer` / `-p` | Установить HashiCorp Packer вместе с настройкой гипервизора. |
| `--romfile_version TEXT` | Версия ROM-файла сетевого интерфейса для установки. |

## Bootstrap

После настройки локальной машины как гипервизора выполните процедуру bootstrap для развёртывания платформы:

```bash
exordos bootstrap -i <version> -f -m core --ssh-public-key /path/to/public/key
```

где `<version>` — версия платформы для развёртывания (например, `0.0.6`). Доступные версии можно посмотреть на [странице релизов](https://github.com/exordos/exordos_core/releases).

Платформу можно запустить как из **локальной сборки** (локально собранный образ), так и из **удалённого репозитория** (готовый образ, загруженный из официального репозитория).

**Пример с локальной сборкой:**

```bash
exordos bootstrap -i /path/to/exordos-core.raw -m core
```

**Пример с удалённым репозиторием (по умолчанию):**

```bash
exordos bootstrap -i https://repository.exordos.com/exordos-elements/core/0.0.6/ -m core
```

### Основные параметры

Выполните `exordos bootstrap --help`, чтобы просмотреть все доступные параметры. Наиболее важные из них:

| Параметр | Описание |
|---|---|
| `--profile` | Профиль инсталляции: `develop`, `small`, `medium`, `large` или `legacy`. По умолчанию: `small`. |
| `--cidr IPV4NETWORK` | CIDR основной сети платформы. По умолчанию: `10.20.0.0/22`. |
| `--core-ip IPV4ADDRESS` | IP-адрес основной виртуальной машины. Если не задан, используется второй адрес из `--cidr`. |
| `--admin-password TEXT` | Пароль администратора. Если не задан, генерируется автоматически. |
| `--save-admin-password-file TEXT` | Сохранить пароль администратора в файл вместо вывода в консоль. |
| `--ssh-public-key PATH` | Путь к публичному SSH-ключу для добавления в виртуальную машину. Можно указать несколько раз. |
| `--hyper-connection-uri TEXT` | URI подключения к гипервизору, например `qemu+tcp://10.0.0.1/system` или `qemu+ssh://user@10.0.0.1/system`. |
| `--hyper-storage-pool TEXT` | Пул хранилища libvirt для дисков виртуальных машин. По умолчанию: `default`. |
| `--force` / `-f` | Принудительная пересборка, если результат уже существует. |

## Использование

После завершения `exordos bootstrap` платформа запущена и готова к работе. Команда выводит учётные данные администратора в консоль (либо сохраняет их в файл, если был указан `--save-admin-password-file`).

### Доступ по SSH

Если при bootstrap был указан публичный SSH-ключ через `--ssh-public-key`, можно подключиться к основной виртуальной машине напрямую:

```bash
ssh ubuntu@10.20.0.2
```

### Доступ через API

Используйте учётные данные администратора для получения токена доступа от сервиса IAM:

```bash
curl --location 'http://10.20.0.2:11010/v1/iam/clients/00000000-0000-0000-0000-000000000000/actions/get_token/invoke' \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=password' \
    --data-urlencode 'username=<ADMIN_USERNAME>' \
    --data-urlencode 'password=<ADMIN_PASSWORD>' \
    --data-urlencode 'client_id=ExordosCoreClientId' \
    --data-urlencode 'client_secret=ExordosCoreSecret' \
    --data-urlencode 'scope=' \
    --data-urlencode 'ttl=86400'
```

В ответе содержится поле `access_token`. Используйте этот токен как `Bearer`-токен во всех последующих запросах к API.

### Доступ через CLI

Настройте CLI `exordos`, зарегистрировав realm и контекст с учётными данными администратора:

```bash
exordos settings set-realm local --endpoint http://10.20.0.2:11010 --current
exordos settings set-context local --name admin -u <ADMIN_USERNAME> -p <ADMIN_PASSWORD> --current
```

- `set-realm` — регистрирует endpoint платформы под именем `local` и устанавливает его как активный realm.
- `set-context` — создаёт именованный контекст с учётными данными администратора и устанавливает его как активный.

После настройки можно управлять платформой с помощью команд `exordos`, например:

```bash
exordos compute hypervisors list
exordos elements list
```
