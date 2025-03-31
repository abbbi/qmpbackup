#!/usr/bin/env python3
"""
Copyright (C) 2022  Michael Ablassmeier

Authors:
 Michael Ablassmeier <abi@grinser.de>

This work is licensed under the terms of the GNU GPL, version 3.  See
the LICENSE file in the top-level directory.
"""
import os
import logging
from time import sleep
import asyncio
from qemu.qmp import EventListener, qmp_client
from libqmpbackup import fs


class QmpCommon:
    """Common functions"""

    def __init__(self, qmp):
        self.qmp = qmp
        self.log = logging.getLogger(__name__)

    async def show_vm_state(self):
        """Show and check if virtual machine is in required
        state"""
        status = await self.qmp.execute("query-status")
        if status["running"] is False and not status["status"] in (
            "prelaunch",
            "paused",
        ):
            raise RuntimeError(f"VM not ready for backup, state: [{status}]")
        self.log.info("VM is in state: [%s]", status["status"])

    async def show_name(self):
        """Show qemu version"""
        name = await self.qmp.execute("query-name")
        if name:
            self.log.info("VM Name: [%s]", name["name"])

    def show_version(self):
        """Show name of VM; if setn"""
        hv_version = self.qmp._greeting._raw["QMP"]  # pylint: disable=W0212
        qemu = hv_version["version"]["qemu"]
        self.log.info(
            "Qemu version: [%s.%s.%s] [%s]",
            qemu["major"],
            qemu["micro"],
            qemu["minor"],
            hv_version["version"]["package"],
        )

    @staticmethod
    def transaction_action(action, **kwargs):
        """Return transaction action object"""
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

    async def prepare_target_devices(self, argv, devices, target_files):
        """Create the required target devices for blockev-backup
        operation"""
        self.log.info("Attach backup target devices to virtual machine")
        for device in devices:
            target = target_files[device.node]
            targetdev = f"qmpbackup-{device.node}"

            args = {
                "driver": device.format,
                "node-name": targetdev,
                "file": {
                    "driver": "file",
                    "filename": target,
                    "aio": argv.blockdev_aio,
                },
            }

            if argv.blockdev_disable_cache is True:
                nocache = {"cache": {"direct": False, "no-flush": False}}
                args = args | nocache
                args["file"] = args["file"] | nocache

            await self.qmp.execute(
                "blockdev-add",
                arguments=args,
            )

    async def prepare_fleece_devices(self, devices, target_files):
        """Create the required fleece devices for blockev-backup
        operation"""
        self.log.info("Attach fleece devices to virtual machine")
        for device in devices:
            target = target_files[device.node]
            targetdev = f"qmpbackup-{device.node}-fleece"

            args = {
                "driver": device.format,
                "node-name": targetdev,
                "file": {
                    "driver": "file",
                    "filename": target,
                },
            }

            await self.qmp.execute(
                "blockdev-add",
                arguments=args,
            )

    async def remove_target_devices(self, devices):
        """Cleanup named devices after executing blockdev-backup
        operation"""
        self.log.info("Removing backup target devices from virtual machine")
        for device in devices:
            targetdev = f"qmpbackup-{device.node}"

            await self.qmp.execute(
                "blockdev-del",
                arguments={
                    "node-name": targetdev,
                },
            )

    async def remove_fleece_devices(self, devices):
        """Cleanup named devices after executing blockdev-backup
        operation"""
        self.log.info("Removing fleece devices from virtual machine")
        for device in devices:
            targetdev = f"qmpbackup-{device.node}-fleece"

            await self.qmp.execute(
                "blockdev-del",
                arguments={
                    "node-name": targetdev,
                },
            )

    async def remove_cbw_devices(self, devices):
        """Cleanup named devices after executing blockdev-backup
        operation"""
        self.log.info("Removing cbw devices from virtual machine")
        for device in devices:
            targetdev = f"qmpbackup-{device.node}-cbw"

            await self.qmp.execute(
                "blockdev-del",
                arguments={
                    "node-name": targetdev,
                },
            )

    async def blockdev_replace(self, devices, action):
        """Issue qom command to switch disk device to copy-before-write filter"""
        self.log.info("Activate copy-before-write filter")
        if action == "disable":
            self.log.info("Activate copy-before-write filter")
        else:
            self.log.info("Disable copy-before-write filter")
        for device in devices:
            target = f"qmpbackup-{device.node}-cbw"
            if action == "disable":
                target = device.node
            await self.qmp.execute(
                "qom-set",
                arguments={
                    "path": device.qdev,
                    "property": "drive",
                    "value": target,
                },
            )

    async def add_cbw_device(self, argv, devices, uuid):
        """Add copy-before-write device operation"""
        self.log.info("Adding cbw devices to virtual machine")
        bitmap_prefix = "qmpbackup"
        if argv.level == "copy":
            bitmap_prefix = f"qmpbackup-{argv.level}"
        for device in devices:
            cbwopt = {
                "driver": "copy-before-write",
                "node-name": f"qmpbackup-{device.node}-cbw",
                "file": device.node,
                "target": f"qmpbackup-{device.node}-fleece",
                "on-cbw-error": "break-snapshot",
                "cbw-timeout": 45,
            }
            if device.has_bitmap and argv.level in ("inc", "diff"):
                bitmap = f"{bitmap_prefix}-{device.node}-{uuid}"
                cbwopt["bitmap"] = {
                    "node": device.node,
                    "name": bitmap,
                }

            await self.qmp.execute("blockdev-add", arguments=cbwopt)

    def prepare_transaction(self, argv, devices, uuid):
        """Prepare transaction steps"""
        sync = "full"
        if argv.level == "inc":
            sync = "incremental"

        bitmap_prefix = "qmpbackup"
        persistent = True
        if argv.level == "copy":
            self.log.info("Copy backup: no persistent bitmap will be created.")
            bitmap_prefix = f"qmpbackup-{argv.level}"
            persistent = False

        actions = []
        for device in devices:
            targetdev = f"qmpbackup-{device.node}"
            bitmap = f"{bitmap_prefix}-{device.node}-{uuid}"
            job_id = f"qmpbackup.{device.node}.{os.path.basename(device.filename)}"

            if (
                not device.has_bitmap
                and device.format != "raw"
                and argv.level in ("full", "copy")
                or device.has_bitmap
                and argv.level in ("copy")
            ):
                self.log.info(
                    "Creating new bitmap: [%s] for device [%s]", bitmap, device.node
                )
                actions.append(
                    self.transaction_bitmap_add(
                        device.node, bitmap, persistent=persistent
                    )
                )

            if device.has_bitmap and argv.level in ("full") and device.format != "raw":
                self.log.info(
                    "Clearing existing bitmap [%s] for device: [%s:%s]",
                    bitmap,
                    device.node,
                    os.path.basename(device.filename),
                )
                actions.append(self.transaction_bitmap_clear(device.node, bitmap))

            compress = argv.compress
            if device.format == "raw" and compress:
                compress = False
                self.log.info("Disabling compression for raw device: [%s]", device.node)

            if argv.level in ("full", "copy") or (
                argv.level == "inc" and device.format == "raw"
            ):
                actions.append(
                    self.transaction_action(
                        "blockdev-backup",
                        device=device.node,
                        target=targetdev,
                        sync=sync,
                        job_id=job_id,
                        speed=argv.speed_limit,
                        compress=compress,
                    )
                )
            else:
                actions.append(
                    self.transaction_action(
                        "blockdev-backup",
                        bitmap=bitmap,
                        device=device.node,
                        target=targetdev,
                        sync=sync,
                        job_id=job_id,
                        speed=argv.speed_limit,
                        compress=argv.compress,
                    )
                )

        self.log.debug("Created transaction: %s", actions)

        return actions

    async def backup(self, argv, devices, qga, uuid):
        """Start backup transaction, while backup is active,
        watch for block status"""

        def job_filter(event) -> bool:
            event_data = event.get("data", {})
            event_job_id = event_data.get("id")
            return event_job_id.startswith("qmpbackup")

        listener = EventListener(
            (
                "BLOCK_JOB_COMPLETED",
                job_filter,
                "BLOCK_JOB_CANCELLED",
                job_filter,
                "BLOCK_JOB_ERROR",
                job_filter,
                "BLOCK_JOB_READY",
                job_filter,
                "BLOCK_JOB_PENDING",
                job_filter,
                "JOB_STATUS_CHANGE",
                job_filter,
            )
        )

        finished = 0
        actions = self.prepare_transaction(argv, devices, uuid)
        with self.qmp.listen(listener):
            await self.qmp.execute("transaction", arguments={"actions": actions})
            _ = asyncio.create_task(self.progress(), name="progress")
            if qga is not False:
                fs.thaw(qga)
            async for event in listener:
                if event["event"] in ("BLOCK_JOB_CANCELLED", "BLOCK_JOB_ERROR"):
                    raise RuntimeError(
                        "Block job failed for device "
                        f"[{event['data']['device']}]: [{event['event']}]",
                    )
                if event["event"] == "BLOCK_JOB_COMPLETED":
                    finished += 1
                    self.log.info("Block job [%s] finished", event["data"]["device"])
                if len(devices) == finished:
                    self.log.info("All backups finished")
                    break

    async def do_query_block(self):
        """Return list of attached block devices"""
        return await self.qmp.execute("query-block")

    async def remove_bitmaps(self, blockdev, prefix="qmpbackup", uuid=""):
        """Remove existing bitmaps for block devices"""
        for dev in blockdev:
            if not dev.has_bitmap:
                self.log.info("No bitmap set for device %s", dev.node)
                continue

            for bitmap in dev.bitmaps:
                bitmap_name = bitmap["name"]
                self.log.debug("Bitmap name: %s", bitmap_name)
                if prefix not in bitmap_name:
                    self.log.debug(
                        "Ignoring bitmap: [%s] not matching prefix [%s]",
                        prefix,
                        bitmap_name,
                    )
                    continue
                if uuid != "" and not bitmap_name.endswith(uuid):
                    self.log.debug(
                        "Ignoring bitmap: [%s] not matching uuid [%s]",
                        bitmap_name,
                        uuid,
                    )
                    continue
                self.log.info("Removing bitmap: %s", bitmap_name)
                await self.qmp.execute(
                    "block-dirty-bitmap-remove",
                    arguments={"node": dev.node, "name": bitmap_name},
                )

    async def progress(self):
        """Report progress for active block job"""
        while True:
            sleep(1)
            try:
                jobs = await self.qmp.execute("query-block-jobs")
            except qmp_client.ExecInterruptedError:
                return
            if len(jobs) == 0:
                return
            for job in jobs:
                if not job["device"].startswith("qmpbackup"):
                    continue
                if job["status"] != "running":
                    continue
                prog = [
                    round(job["offset"] / job["len"] * 100) if job["offset"] != 0 else 0
                ]
                self.log.info(
                    "[%s] Wrote Offset: %s%% (%s of %s)",
                    job["device"],
                    prog[0],
                    job["offset"],
                    job["len"],
                )
