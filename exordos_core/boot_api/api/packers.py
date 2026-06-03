#    Copyright 2025 Genesis Corporation.
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

from restalchemy.api import packers

from exordos_core.boot_api.dm import models
from exordos_core.compute import constants as nc

_from_net_template = """#!ipxe
:kernel
kernel {kernel} showopts ip=dhcp net.ifnames=0 biosdevname=0 gc_boot_api={gc_boot_api} console=ttyS0,115200 console=tty0 || goto kernel

:initrd
initrd {initrd} || goto initrd
boot
"""


_from_hd_template = """#!ipxe

echo Boot from local disk {disk_number}
sanboot --no-describe --drive 0x8{disk_number}
"""


class IPXEPacker(packers.JSONPacker):
    def pack(self, obj: models.MachineNetboot):
        boot = nc.BootAlternative[obj.boot]

        if boot == nc.BootAlternative.network:
            return _from_net_template.format(
                gc_boot_api=obj.gc_boot_api,
                kernel=obj.kernel,
                initrd=obj.initrd,
            )
        elif boot.boot_type == "hd":
            return _from_hd_template.format(disk_number=boot.value[2])

        raise ValueError(f"Invalid boot alternative: {boot}")
