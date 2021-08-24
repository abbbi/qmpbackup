import string
import libqmpbackup.qmp as qmp


class QmpCommon:
    """QmpCommon class, based on qemu.py by the qemu
    project
    """

    def __init__(self, log, socket, negotiate=True):
        """regular QMP for all vm commands"""
        self._qmp = qmp.QEMUMonitorProtocol(socket)
        self._qmp.connect(negotiate=negotiate)

        self._log = log
        self._events = []

    def get_qmp_event(self, wait=False):
        """Poll for one queued QMP events and return it"""
        if len(self._events) > 0:
            return self._events.pop(0)
        return self._qmp.pull_event(wait=wait)

    def get_qmp_events(self, wait=False):
        """Poll for queued QMP events and return a list of dicts"""
        events = self._qmp.get_events(wait=wait)
        events.extend(self._events)
        del self._events[:]
        self._qmp.clear_events()
        return events

    def event_wait(self, name, timeout=60.0, match=None):
        """wait for events
        Test if 'match' is a recursive subset of 'event'
        """

        def event_match(event, match=None):
            if match is None:
                return True

            for key in match:
                if key in event:
                    if isinstance(event[key], dict):
                        if not event_match(event[key], match[key]):
                            return False
                    elif event[key] != match[key]:
                        return False
                else:
                    return False

            return True

        for event in self._events:
            if (event["event"] == name) and event_match(event, match):
                self._events.remove(event)
                return event

        while True:
            event = self._qmp.pull_event(wait=timeout)
            if (event["event"] == name) and event_match(event, match):
                return event
            self._events.append(event)

        return None

    underscore_to_dash = str.maketrans("_", "-")

    def qmp(self, cmd, conv_keys=True, **args):
        """Invoke a QMP command and return the result dict"""
        qmp_args = dict()
        for k in args.keys():
            if conv_keys:
                qmp_args[k.translate(self.underscore_to_dash)] = args[k]
            else:
                qmp_args[k] = args[k]

        return self._qmp.cmd(cmd, args=qmp_args)

    def command(self, cmd, conv_keys=True, **args):
        reply = self.qmp(cmd, conv_keys, **args)
        if self.check_qmp_return(reply):
            # on empty return {} we assume True
            if reply["return"] == None:
                return True
            else:
                return reply["return"]

    def check_qmp_return(self, reply):
        if reply is None:
            raise Exception("Monitor is closed")
        if "error" in reply:
            raise Exception(reply["error"]["desc"])

        return True

    def transaction_action(self, action, **kwargs):
        return {
            "type": action,
            "data": dict((k.replace("_", "-"), v) for k, v in kwargs.items()),
        }

    def transaction_bitmap_clear(self, node, name, **kwargs):
        """Return transaction action object for bitmap clear"""
        return self.transaction_action(
            "block-dirty-bitmap-clear", node=node, name=name, **kwargs
        )

    def transaction_bitmap_add(self, node, name, **kwargs):
        """Return transaction action object for bitmap add"""
        return self.transaction_action(
            "block-dirty-bitmap-add", node=node, name=name, **kwargs
        )

    def transaction_snapshot_create(self, node, target, **kwargs):
        """Return transaction action object for snapshot create"""
        return self.transaction_action(
            "blockdev-snapshot-sync", device=node, snapshot_file="/tmp/test"
        )

    def remove_bitmap(self, node="ide0-hd0", name="qmpbackup"):
        return self.check_qmp_return(
            self.qmp("block-dirty-bitmap-remove", node=node, name=name)
        )

    def create_snapshot_and_bitmap(
        self, node="ide0-hd0", bitmap="bitmap0", snapshot_target="snapshot1"
    ):
        """Live backup via snapshot, allows to backup the image file in place
        without having it to dump somwhere else
        """
        reply = self.qmp(
            "transaction",
            actions=[
                self.transaction_bitmap_add(node, bitmap),
                self.transaction_snapshot_create(node, snapshot_target),
            ],
        )
        return self.check_qmp_return(reply)

    def block_commit(self, node="ide0-hd0"):
        """commit back possible snapshots"""
        self.qmp("block-commit", device=node)
        reply = self.event_wait(
            name="BLOCK_JOB_READY", match={"data": {"device": "ide0-hd0"}}
        )

        if reply:
            self.qmp("block-job-complete", device=node)
            return self.event_wait(
                name="BLOCK_JOB_COMPLETED", match={"data": {"device": node}}
            )

    def do_full_backup_with_bitmap(
        self, has_bitmap, bitmap, device, target, sync="full"
    ):
        """Backup method for full backup
        "Live" method, (A):
            - Create a bitmap
            - Use a single transaction to:
            - Create a full backup using drive-backup sync=full
            - Reset the bitmap

        directly copies the image to desired place
        """
        actions = []
        if has_bitmap == True:
            """clear existing bitmap, start new chain"""
            actions.append(self.transaction_bitmap_clear(device, bitmap))
        else:
            bitmap = "qmpbackup-%s" % device
            actions.append(self.transaction_bitmap_add(device, bitmap, persistent=True))

        actions.append(self.transaction_bitmap_clear(device, bitmap))
        actions.append(
            self.transaction_action(
                "drive-backup", device=device, target=target, sync=sync
            )
        )

        reply = self.qmp("transaction", actions=actions)
        if self.check_qmp_return(reply):
            return self.event_wait(
                timeout="3200",
                name="BLOCK_JOB_COMPLETED",
                match={"data": {"device": device}},
            )

    def do_qmp_backup(self, **kwargs):
        """Issue backup pcommand via qmp protocol"""
        reply = self.qmp("drive-backup", **kwargs)
        if self.check_qmp_return(reply):
            return self.event_wait(
                timeout="3200",
                name="BLOCK_JOB_COMPLETED",
                match={"data": {"device": kwargs["device"]}},
            )

    def do_query_block(self):
        return self.command("query-block")

    def remove_bitmaps(self, blockdev):
        """Loop through existing devices and bitmaps, remove them"""
        for dev in blockdev:
            if dev.has_bitmap:
                try:
                    for bitmap in dev.bitmaps:
                        if self.remove_bitmap(dev.node, bitmap["name"]):
                            self._log.debug(
                                'Bitmap "%s" for device "%s" removed'
                                % (bitmap["name"], dev.node)
                            )
                except Exception as e:
                    raise
            else:
                self._log.debug("No bitmap set for any device")
        return True
