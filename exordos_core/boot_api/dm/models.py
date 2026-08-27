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

import typing as tp

from restalchemy.dm import types

from exordos_core.compute.dm import models

LOCAL_GC_HOST = "core.local.genesis-core.tech"
LOCAL_GC_BOOT_API = "http://core.local.genesis-core.tech:11013"


class MachineNetboot(models.Machine):
    __custom_properties__ = {
        "gc_host": types.String(max_length=255),
        "gc_boot_api": types.String(max_length=255),
        "kernel": types.AllowNone(types.String(max_length=255)),
        "initrd": types.AllowNone(types.String(max_length=255)),
    }

    def __init__(
        self,
        gc_host: str = LOCAL_GC_HOST,
        gc_boot_api: str = LOCAL_GC_BOOT_API,
        kernel: tp.Optional[str] = None,
        initrd: tp.Optional[str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.set_netboot_params(gc_host, gc_boot_api, kernel, initrd)

    @classmethod
    def restore_row(
        cls,
        row: tp.Mapping[str, tp.Any],
        pour: tp.Any = None,
    ) -> "MachineNetboot":
        # Every read arrives here -- one model, and a page of them alike;
        # `restore_from_storage`, which this class used to override, is
        # only one of the two, and a collection does not go by it.
        #
        # The netboot parameters are not columns of the machine table, so
        # they are never in a row: a restored machine gets the defaults
        # here, and the controller overwrites them with what the
        # installation is configured with.
        obj = super().restore_row(row, pour)
        obj.set_netboot_params(
            LOCAL_GC_HOST,
            LOCAL_GC_BOOT_API,
            None,
            None,
        )
        return obj

    def set_netboot_params(
        self,
        gc_host: str,
        gc_boot_api: str,
        kernel: tp.Optional[str],
        initrd: tp.Optional[str],
    ) -> None:
        self.gc_host = gc_host
        self.gc_boot_api = gc_boot_api

        # Use tftp by default
        if kernel is None:
            kernel = f"tftp://{gc_host}/bios/vmlinuz"
        if initrd is None:
            initrd = f"tftp://{gc_host}/bios/initrd.img"

        self.kernel = kernel
        self.initrd = initrd
