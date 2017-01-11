# Copyright (C) 2016 Linaro Limited
#
# Author: Tyler Baker <tyler.baker@linaro.org>
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

from lava_dispatcher.pipeline.action import (
    Pipeline,
    Action,
    JobError,
)
from lava_dispatcher.pipeline.logical import Boot, RetryAction
from lava_dispatcher.pipeline.actions.boot import BootAction
from lava_dispatcher.pipeline.connections.serial import ConnectDevice
from lava_dispatcher.pipeline.utils.shell import infrastructure_error
from lava_dispatcher.pipeline.utils.strings import substitute


class BootMonitorPyOCD(Boot):

    compatibility = 4  # FIXME: change this to 5 and update test cases

    def __init__(self, parent, parameters):
        super(BootMonitorPyOCD, self).__init__(parent)
        self.action = BootMonitoredPyOCD()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if 'pyocd' not in device['actions']['boot']['methods']:
            return False
        if 'method' not in parameters:
            return False
        if parameters['method'] != 'monitor':
            return False
        if 'board_id' not in device:
            return False
        return True


class BootMonitoredPyOCD(BootAction):

    def __init__(self):
        super(BootMonitoredPyOCD, self).__init__()
        self.name = 'boot_pyocd_image'
        self.description = "boot monitored pyocd image with retry"
        self.summary = "boot monitor with retry"

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.internal_pipeline.add_action(BootMonitoredPyOCDRetry())


class BootMonitoredPyOCDRetry(RetryAction):

    def __init__(self):
        super(BootMonitoredPyOCDRetry, self).__init__()
        self.name = 'boot_pyocd_image'
        self.description = "boot pyocd image using the command line interface"
        self.summary = "boot pyocd image"

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.internal_pipeline.add_action(MonitorPyOCDAction())
        self.internal_pipeline.add_action(ConnectDevice())


class MonitorPyOCDAction(Action):

    def __init__(self):
        super(MonitorPyOCDAction, self).__init__()
        self.name = "monitor-pyocd"
        self.description = "monitor pyocd to boot the image"
        self.summary = "monitor pyocd to boot the image"
        self.sub_command = []

    def validate(self):
        super(MonitorPyOCDAction, self).validate()
        boot = self.job.device['actions']['boot']['methods']['pyocd']
        pyocd_binary = boot['parameters']['command']
        self.errors = infrastructure_error(pyocd_binary)
        self.sub_command = ['flock -o /var/lock/lava-pyocd.lck', pyocd_binary]
        self.sub_command.extend(boot['parameters'].get('options', []))
        if self.job.device['board_id'] == '0000000000':
            self.errors = "board_id unset"
        substitutions = {}
        commands = ['-b ' + self.job.device['board_id']]
        namespace = self.parameters.get('namespace', 'common')
        download_keys = self.data[namespace]['download_action']
        for action in download_keys():
            image_arg = self.get_namespace_data(action='download_action', label=action, key='image_arg')
            action_arg = self.get_namespace_data(action='download_action', label=action, key='file')
            if not image_arg or not action_arg:
                self.errors = "Missing image_arg for %s. " % action
                continue
            substitutions["{%s}" % action] = action_arg
            commands.append(image_arg)
        self.sub_command.extend(substitute(commands, substitutions))
        if not self.sub_command:
            self.errors = "No PyOCD command to execute"

    def run(self, connection, max_end_time, args=None):
        pyocd = ' '.join(self.sub_command)
        self.logger.info("PyOCD command: %s", pyocd)
        if self.run_command(pyocd.split(' ')):
            pass
        else:
            raise JobError("%s command failed" % (self.sub_command))
        res = 'failed' if self.errors else 'success'
        self.set_namespace_data(action='boot', label='shared', key='boot-result', value=res)
        return connection
