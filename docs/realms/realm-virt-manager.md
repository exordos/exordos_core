# Running Exordos Realm with virt-manager

> **Console-access note:** The production realm-node artifact is intended to
> be configured by a parent realm and does **not** provide console access. To
> operate a standalone developer stand, use image exordos-realm-dev.raw.zst.

## Create and run the VM

These commands download the dev image, expand it, and create an
importable disk. Run them on an Ubuntu/Debian KVM host:

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virt-manager zstd
sudo usermod -aG libvirt,kvm "$USER"
# Log out and back in once for the new group membership to take effect.

cd /var/lib/libvirt/images/
# The dev image is published per realm version; pick the latest from
# https://repo.exordos.com/exordos-elements/exordos-realm/
sudo wget -O exordos-realm-dev.raw.zst \
  https://repo.exordos.com/exordos-elements/exordos-realm/0.1.6/images/exordos-realm-dev.raw.zst
sudo zstd --decompress --keep exordos-realm-dev.raw.zst
sudo chown libvirt-qemu:kvm exordos-realm-dev.raw
sudo qemu-img info exordos-realm-dev.raw
```

The host must expose nested KVM to the guest because the realm node runs a
nested core VM. Check the relevant value before creating the VM:

```bash
# Intel hosts
cat /sys/module/kvm_intel/parameters/nested
# AMD hosts
cat /sys/module/kvm_amd/parameters/nested
```

It must print `Y` (or `1`). Enable nested virtualization on the host before
continuing if it does not.

Start virt-manager and select the system libvirt connection:

```bash
virt-manager --connect qemu:///system
```

In the GUI, select **File → New Virtual Machine**, then configure:

1. Select **Import existing disk image**.
2. Select `/var/lib/libvirt/images/exordos-realm-dev.raw`; choose a recent Ubuntu OS
   type (for example, Ubuntu 24.04) if virt-manager asks.
3. Allocate at least **4 vCPUs** and **8192 MiB RAM**; use more resources if the nested core workload requires it.
4. Keep the default NAT network or choose a bridged network appropriate for
   the host.
5. Tick **Customize configuration before install**. In **CPUs**, select
   **Copy host CPU configuration** / host-passthrough so KVM virtualization
   extensions are visible to the guest. Ensure the disk uses the `raw` format
   and virtio bus, then click **Begin Installation**.

The following fully command-line import is equivalent and registers the VM so
it can be opened and managed with virt-manager:

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

# Open the VM's graphical console in virt-manager.
virt-manager --connect qemu:///system --show-domain-console exordos-realm
```

## Test the VM

After starting it, confirm that libvirt reports it as running and that KVM is
available inside the realm node:

```bash
virsh --connect qemu:///system list --all
virsh --connect qemu:///system dominfo exordos-realm

# Run this in the guest console (a DEV_ACCESS image is required to log in).
kvm-ok
```

`virsh` should show `exordos-realm` as `running`; `kvm-ok` should report that
KVM acceleration can be used. For a DEV_ACCESS image, log in as
`ubuntu:ubuntu`.
The production image will wait for `/etc/exordos/realm_spec.json` from its
parent realm rather than offering standalone console access.

## Developer stand

Simulate the managed flow by writing `/etc/exordos/realm_spec.json`
yourself (see the contract in exordos_ecosystem `docs/realm-manager.md`, or you can copy example spec
by `cp /etc/exordos/realm_spec.json.example /etc/exordos/realm_spec.json`) —
the first-boot unit picks it up.

Key names deliberately match the `spec.json` keys consumed by
`exordos_core/cmd/bootstrap.py` / `exordos_core/bootstrap/defaults.py`
(`realm_uuid`, `realm_secret`, `realm_tokens`, `ecosystem_endpoint`,
`admin_password`, `disable_telemetry`), so the image's first-boot script can
merge this file into its baked spec template with minimal mapping.

Next you can view the bootstrap service logs with `sudo journalctl -u exordos-realm-bootstrap`.

And after few seconds you can see work stand by `exordos e e l`.
