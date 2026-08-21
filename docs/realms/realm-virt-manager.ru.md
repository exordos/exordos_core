# Запуск Realm Exordos с virt-manager

> **О консольном доступе:** production-артефакт `realm-node` настраивается
> родительским realm и **не** даёт консольного доступа. Для автономного
> стенда разработчика используйте образ `exordos-realm-dev.raw.zst`.

## Создание и запуск ВМ

Эти команды загружают dev-образ, распаковывают его и создают диск для
импорта. Выполняйте их на KVM-хосте с Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients virt-manager zstd
sudo usermod -aG libvirt,kvm "$USER"
# Выйдите из системы и зайдите снова, чтобы применить новую группу.

cd /var/lib/libvirt/images/
sudo wget -O exordos-realm-dev.raw.zst \
  https://repo.exordos.com/exordos-elements/exordos-realm/latest/images/exordos-realm-dev.raw.zst
sudo zstd --decompress --keep exordos-realm-dev.raw.zst
sudo chown libvirt-qemu:kvm exordos-realm-dev.raw
sudo qemu-img info exordos-realm-dev.raw
```

Realm node сам запускает core-ВМ, то есть внутри гостя работает ещё одна
виртуальная машина. Для этого хост должен отдавать гостю вложенный KVM
(nested KVM). Перед созданием ВМ проверьте, что он включён:

```bash
# Хосты Intel
cat /sys/module/kvm_intel/parameters/nested
# Хосты AMD
cat /sys/module/kvm_amd/parameters/nested
```

Значение должно быть `Y` (или `1`). Если это не так, включите на хосте
вложенную виртуализацию прежде чем продолжать.

Запустите virt-manager и выберите системное подключение libvirt:

```bash
virt-manager --connect qemu:///system
```

В графическом интерфейсе выберите **Файл → Новая виртуальная машина**, затем
настройте:

1. Выберите **Импортировать существующий образ диска**.
2. Укажите `/var/lib/libvirt/images/exordos-realm-dev.raw`; выберите
   подходящий тип ОС Ubuntu (например, Ubuntu 24.04), если virt-manager
   запросит.
3. Выделите не менее **4 vCPU** и **8192 MiB ОЗУ**; увеличьте ресурсы, если
   этого требует вложенная core-нагрузка.
4. Оставьте сеть NAT по умолчанию или выберите мостовую сеть, подходящую
   для хоста.
5. Отметьте **Настроить конфигурацию перед установкой**. В разделе **CPU**
   выберите **Копировать конфигурацию CPU хоста** / host-passthrough, чтобы
   расширения виртуализации KVM были доступны гостю. Убедитесь, что диск
   использует формат `raw` и шину virtio, затем нажмите **Начать установку**.

Полностью эквивалентный вариант на командной строке приведён ниже — он
регистрирует ВМ так, что ей можно управлять через virt-manager:

```bash
virt-install --connect qemu:///system \
  --name exordos-realm \
  --memory 8192 \
  --vcpus 4 \
  --cpu host-passthrough \
  --disk path=/var/lib/libvirt/images/exordos-realm-dev.raw,format=raw,bus=virtio \
  --network network=default,model=virtio \
  --os-variant ubuntu24.04 \
  --graphics spice \
  --video virtio \
  --import --noautoconsole

# Откройте графическую консоль ВМ в virt-manager.
virt-manager --connect qemu:///system --show-domain-console exordos-realm
```

## Проверка ВМ

После запуска убедитесь, что libvirt сообщает о работе ВМ и что KVM доступен
внутри realm node:

```bash
virsh --connect qemu:///system list --all
virsh --connect qemu:///system dominfo exordos-realm

# Выполните в гостевой консоли (для входа требуется образ DEV_ACCESS).
kvm-ok
```

`virsh` должен показать `exordos-realm` в статусе `running`; `kvm-ok` должен
сообщить, что ускорение KVM доступно. Для образа DEV_ACCESS войдите как
`ubuntu:ubuntu`.
Production-образ вместо консольного доступа будет ждать
`/etc/exordos/realm_spec.json` от своего родительского realm.

## Стенд разработчика

Смоделируйте создание стенда, написав `/etc/exordos/realm_spec.json`
самостоятельно (смотрите контракт в exordos_ecosystem `docs/realm-manager.md`, или вы можете скопировать пример спецификации
с помощью `cp /etc/exordos/realm_spec.json.example /etc/exordos/realm_spec.json`) —
модуль первой загрузки распознает его.

Имена ключей намеренно совпадают с ключами `spec.json`, используемыми
`exordos_core/cmd/bootstrap.py` / `exordos_core/bootstrap/defaults.py`
(`realm_uuid`, `realm_secret`, `realm_tokens`, `ecosystem_endpoint`,
`admin_password`, `disable_telemetry`), чтобы скрипт первой загрузки образа мог
объединить этот файл с его готовым шаблоном спецификации с минимальным сопоставлением.

Далее вы можете просмотреть логи службы bootstrap с помощью `sudo journalctl -u exordos-realm-bootstrap`.

И через несколько секунд вы можете увидеть рабочий стенд, выполнив команду `exordos e e l`.
