"""
 QEMU Guest Agent Client

 Copyright (C) 2022 Michael Ablassmeier <abi@grinser.de>
 Copyright (C) 2012 Ryota Ozaki <ozaki.ryota@gmail.com>

 This work is licensed under the terms of the GNU GPL, version 2.  See
 the COPYING file in the top-level directory.

"""

import random
import libqmpbackup.qa as qmp


class QemuGuestAgent(qmp.QEMUMonitorProtocol):
    """Wrap functions"""

    def __getattr__(self, name):
        def wrapper(**kwds):
            return self.command("guest-" + name.replace("_", "-"), **kwds)

        return wrapper


class QemuGuestAgentClient:
    """Guest Agent functions"""

    error = QemuGuestAgent.error

    def __init__(self, address):
        self.qga = QemuGuestAgent(address)
        self.qga.connect()

    def sync(self, timeout=3):
        """Avoid being blocked forever"""
        if not self.ping(timeout):
            raise EnvironmentError("Agent seems not alive")
        uid = random.randint(0, (1 << 32) - 1)
        while True:
            ret = self.qga.sync(id=uid)
            if isinstance(ret, int) and int(ret) == uid:
                break

    def info(self):
        """Return supported commands"""
        info = self.qga.info()
        return [c["name"] for c in info["supported_commands"] if c["enabled"]]

    def ping(self, timeout):
        """Ping the guest agent, see if its alive"""
        self.qga.settimeout(timeout)
        try:
            self.qga.ping()
        except self.qga.timeout:
            return False
        return True

    def fsfreeze(self, cmd):
        """Freeze / thaw filesystem"""
        if cmd not in ["status", "freeze", "thaw"]:
            raise RuntimeError("Invalid command: " + cmd)

        return getattr(self.qga, "fsfreeze" + "_" + cmd)()
