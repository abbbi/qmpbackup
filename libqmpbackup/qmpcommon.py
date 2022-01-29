#!/usr/bin/env python3
"""
 Copyright (C) 2022  Michael Ablassmeier

 Authors:
  Michael Ablassmeier <abi@grinser.de>

 This work is licensed under the terms of the GNU GPL, version 3.  See
 the LICENSE file in the top-level directory.
"""
import os
import sys
from qemu.qmp import QMPClient, EventListener
from time import sleep, time
from datetime import datetime


class QmpCommon:
    def __init__(self, qmp, log):
        self.qmp = qmp
        self.log = log

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

    def prepare_transaction(self, devices, level, backupdir):
        """Prepare transaction steps"""
        prefix = "FULL"
        sync = "full"
        if level == "inc":
            prefix = "INC"
            sync = "incremental"

        actions = []
        for device in devices:
            timestamp = int(time())
            os.makedirs(f"{backupdir}/{device.node}/", exist_ok=True)
            target = "%s/%s/%s-%s" % (backupdir, device.node, prefix, timestamp)

            bitmap = f"qmpbackup-{device.node}"
            job_id = f"{device.node}"
            if not device.has_bitmap and level == "full":
                self.log.debug("Creating new bitmap")
                actions.append(
                    self.transaction_bitmap_add(device.node, bitmap, persistent=True)
                )

            if device.has_bitmap and level == "full":
                self.log.debug("Clearing existing bitmap")
                actions.append(self.transaction_bitmap_clear(device.node, bitmap))

            if level == "full":
                actions.append(
                    self.transaction_action(
                        "drive-backup",
                        device=device.node,
                        target=target,
                        sync=sync,
                        job_id=job_id,
                    )
                )
            else:
                actions.append(
                    self.transaction_action(
                        "drive-backup",
                        bitmap=bitmap,
                        device=device.node,
                        target=target,
                        sync=sync,
                        job_id=job_id,
                    )
                )

        self.log.debug("Created transaction: %s", actions)

        return actions

    async def backup(self, devices, level, backupdir, qga, common):
        """Start backup transaction, while backup is active,
        watch for block status"""
        actions = self.prepare_transaction(devices, level, backupdir)
        listener = EventListener()
        with self.qmp.listen(listener):
            await self.qmp.execute("transaction", arguments={"actions": actions})
            if qga is not False:
                common.thaw(qga)
            async for event in listener:
                if event["event"] == "BLOCK_JOB_COMPLETED":
                    self.log.info("Saved all disks")
                    break
                if event["event"] in ("BLOCK_JOB_ERROR", "BLOCK_JOB_CANCELLED"):
                    raise RuntimeError(
                        f"Error during backup operation: {event['event']}"
                    )

                while True:
                    jobs = await self.qmp.execute("query-block-jobs")
                    if not jobs:
                        break
                    self.progress(jobs, devices)
                    sleep(1)

    async def do_query_block(self):
        devices = await self.qmp.execute("query-block")
        return devices

    async def remove_bitmaps(self, blockdev):
        for dev in blockdev:
            if not dev.has_bitmap:
                self.log.info("No bitmap set for device %s", dev.node)
                continue

            for bitmap in dev.bitmaps:
                bitmap_name = bitmap["name"]
                self.log.info("Bitmap name: %s", bitmap_name)
                if not "qmpbackup" in bitmap_name:
                    self.log.info("Ignoring bitmap: %s", bitmap_name)
                    continue
                self.log.info("Removing bitmap: %s", f"qmpbackup-{dev.node}")
                await self.qmp.execute(
                    "block-dirty-bitmap-remove",
                    arguments={"node": dev.node, "name": f"qmpbackup-{dev.node}"},
                )

    def progress(self, jobs, devices):
        for device in devices:
            for job in jobs:
                if job["device"] == device.node:
                    pr = [
                        round(job["offset"] / job["len"] * 100)
                        if job["offset"] != 0
                        else 0
                    ]
                    self.log.info(
                        "[%s] Wrote Offset: %s%% (%s of %s)",
                        job["device"],
                        pr[0],
                        job["offset"],
                        job["len"],
                    )
