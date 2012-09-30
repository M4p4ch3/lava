# Copyright (C) 2011 Linaro Limited
#
# Author: Michael Hudson-Doyle <michael.hudson@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import contextlib
import logging

from lava_dispatcher.device.target import (
    Target
)
from lava_dispatcher.client.lmc_utils import (
    generate_image,
    image_partition_mounted,
    )
from lava_dispatcher.downloader import (
    download_image,
    )
from lava_dispatcher.utils import (
    logging_spawn,
    )


class QEMUTarget(Target):

    def __init__(self, context, config):
        super(QEMUTarget, self).__init__(context, config)
        self._sd_image = None

    def _customize_ubuntu(self):
        root_part = self.config.root_part
        with image_partition_mounted(self._sd_image, root_part) as mnt:
            with open('%s/etc/hostname' % mnt, 'w') as f:
                f.write('%s\n' % self.config.tester_hostname)

    def deploy_linaro(self, hwpack=None, rootfs=None):
        odir = self.scratch_dir
        self._sd_image = generate_image(self, hwpack, rootfs, odir)
        self._customize_ubuntu()

    def deploy_linaro_prebuilt(self, image):
        self._sd_image = download_image(image, self.context)
        self._customize_ubuntu()

    @contextlib.contextmanager
    def file_system(self, partition, directory):
        with image_partition_mounted(self._sd_image, partition) as mntdir:
            yield '%s/%s' % (mntdir, directory)

    def power_off(self, proc):
        if proc is not None:
            proc.close()

    def power_on(self):
        qemu_cmd = ('%s -M %s -drive if=%s,cache=writeback,file=%s '
                    '-clock unix -device usb-kbd -device usb-mouse -usb '
                    '-device usb-net,netdev=mynet -netdev user,id=mynet '
                    '-net nic -net user -nographic') % (
            self.context.config.default_qemu_binary,
            self.config.qemu_machine_type,
            self.config.qemu_drive_interface,
            self._sd_image)
        logging.info('launching qemu with command %r' % qemu_cmd)
        proc = logging_spawn(qemu_cmd, logfile=self.sio, timeout=None)
        return proc

target_class = QEMUTarget
