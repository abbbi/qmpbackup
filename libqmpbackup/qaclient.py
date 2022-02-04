#!/usr/bin/python

# QEMU Guest Agent Client
#
# Copyright (C) 2012 Ryota Ozaki <ozaki.ryota@gmail.com>
#
# This work is licensed under the terms of the GNU GPL, version 2.  See
# the COPYING file in the top-level directory.
#
# Usage:
#
# Start QEMU with:
#
# # qemu [...] -chardev socket,path=/tmp/qga.sock,server,nowait,id=qga0 \
#   -device virtio-serial -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0
import random
import libqmpbackup.qa as qmp


class QemuGuestAgent(qmp.QEMUMonitorProtocol):
    def __getattr__(self, name):
        def wrapper(**kwds):
            return self.command("guest-" + name.replace("_", "-"), **kwds)

        return wrapper


class QemuGuestAgentClient:
    error = QemuGuestAgent.error

    def __init__(self, address):
        self.qga = QemuGuestAgent(address)
        self.qga.connect()

    def sync(self, timeout=3):
        # Avoid being blocked forever
        if not self.ping(timeout):
            raise EnvironmentError("Agent seems not alive")
        uid = random.randint(0, (1 << 32) - 1)
        while True:
            ret = self.qga.sync(id=uid)
            if isinstance(ret, int) and int(ret) == uid:
                break

    def info(self):
        info = self.qga.info()
        return [c["name"] for c in info["supported_commands"] if c["enabled"]]

    def ping(self, timeout):
        self.qga.settimeout(timeout)
        try:
            self.qga.ping()
        except self.qga.timeout:
            return False
        return True

    def fsfreeze(self, cmd):
        if cmd not in ["status", "freeze", "thaw"]:
            raise Exception("Invalid command: " + cmd)

        return getattr(self.qga, "fsfreeze" + "_" + cmd)()

    def fstrim(self, minimum=0):
        return getattr(self.qga, "fstrim")(minimum=minimum)
