#!/usr/bin/env python3
"""
Copyright (C) 2022  Michael Ablassmeier

Authors:
 Michael Ablassmeier <abi@grinser.de>

This work is licensed under the terms of the GNU GPL, version 3.  See
the LICENSE file in the top-level directory.
"""
import os
import asyncio
import sys
import logging
from time import sleep
from libqmpbackup import fs
from libqmpbackup.lib import json_pp
from qemu.qmp import protocol


class QmpCommon:
    """Common functions"""

    def __init__(self, qmp, socket, connection_retry):
        self.qmp = qmp
        self.log = logging.getLogger(__name__)
        self.socket = socket
        self.connection_retry = connection_retry
        self.stop_backup = False

    async def cancel_jobs(self):
        """Dismiss or cancel all running block jobs. Use new connection"""
        self.log.info("Cancelling all block jobs")
        limit = 60
        for retry in range(limit):
            if retry >= limit:
                self.log.warning("Unable to cancel leftover backup jobs")
                break
            jobs = await self._execute("query-block-jobs")
            if len(jobs) == 0:
                self.log.info("All jobs stopped")
                return
            for job in jobs:
                if job["type"] != "backup" or not job["device"].startswith("qmpbackup"):
                    continue
                if job["status"] == "concluded":
                    await self._execute(
                        "block-job-dismiss",
                        arguments={"id": job["device"]},
                    )
                else:
                    await self._execute(
                        "block-job-cancel",
                        arguments={
                            "device": job["device"],
                            "force": True,
                        },
                    )
            await asyncio.sleep(1)

    async def _connect(self):
        self.log.debug("Connecting QMP socket: [%s]", self.socket)
        max_retry = self.connection_retry
        retry = 0
        for _ in range(0, max_retry):
            try:
                await self.qmp.connect(self.socket)
                break
            except protocol.ConnectError as errmsg:
                if retry <= max_retry:
                    self.log.fatal(
                        "Can't connect QMP socket [%s]: %s, retry: [%s]",
                        self.socket,
                        errmsg,
                        retry,
                    )
                    retry += 1
                    sleep(1)
                    continue

                self.log.fatal(
                    "Unable to connect QMP socket [%s] after [%s] retries: [%s] giving up",
                    self.socket,
                    errmsg,
                    retry,
                )
                sys.exit(1)

    async def _disconnect(self):
        self.log.debug("Disconnect QMP socket: [%s]", self.socket)
        await self.qmp.disconnect()

    async def _execute(self, *args, **kwargs):
        await self._connect()
        try:
            res = await self.qmp.execute(*args, **kwargs)
        except Exception as errmsg:
            raise RuntimeError(f"Error executing qmp command: {errmsg}") from errmsg
        finally:
            await self._disconnect()
        return res

    async def show_vm_state(self):
        """Show and check if virtual machine is in required
        state"""
        status = await self._execute("query-status")
        if status["running"] is False and not status["status"] in (
            "prelaunch",
            "paused",
        ):
            raise RuntimeError(f"VM not ready for backup, state: [{status}]")
        self.log.info("VM is in state: [%s]", status["status"])

    async def show_name(self):
        """Show qemu version"""
        name = await self._execute("query-name")
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

    async def prepare_fleece_devices(self, devices, target_files):
        """Create the required fleece devices for blockev-backup
        operation"""
        self.log.info("Attach fleece devices to virtual machine")
        for device in devices:
            target = target_files[device.node]
            targetdev = f"qmpbackup-{device.node_safe}-fleece"

            args = {
                "driver": device.format,
                "node-name": targetdev,
                "file": {
                    "driver": "file",
                    "filename": target,
                },
            }

            await self._execute(
                "blockdev-add",
                arguments=args,
            )

    async def add_snapshot_access_devices(self, devices):
        """Prepare snapshot-access devices required for backup using
        image fleecing technique"""
        self.log.info("Add snapshot-access devices.")
        for device in devices:
            snap = {
                "driver": "snapshot-access",
                "file": f"qmpbackup-{device.node_safe}-cbw",
                "node-name": f"qmpbackup-{device.node_safe}-snap",
            }
            await self._execute(
                "blockdev-add",
                arguments=snap,
            )

    async def add_cbw_device(self, argv, devices, uuid):
        """Add copy-before-write device operation"""
        self.log.info("Adding cbw devices to virtual machine")
        bitmap_prefix = "qmpbackup"
        if argv.level == "copy":
            bitmap_prefix = f"qmpbackup-{argv.level}"
        for device in devices:
            node = device.node
            if device.child_device is not None:
                node = device.child_device

            cbwopt = {
                "driver": "copy-before-write",
                "node-name": f"qmpbackup-{device.node_safe}-cbw",
                "file": node,
                "target": f"qmpbackup-{device.node_safe}-fleece",
                "on-cbw-error": "break-snapshot",
                "cbw-timeout": 45,
            }
            if device.has_bitmap and argv.level in ("inc", "diff"):
                bitmap = f"{bitmap_prefix}-{device.device}-{uuid}"
                cbwopt["bitmap"] = {
                    "node": node,
                    "name": bitmap,
                }

            await self._execute("blockdev-add", arguments=cbwopt)

    async def blockdev_replace(self, devices, action):
        """Issue qom command to switch disk device to copy-before-write filter"""
        if action == "disable":
            self.log.info("Disable copy-before-write filter")
        else:
            self.log.info("Activate copy-before-write filter")
        for device in devices:
            target = f"qmpbackup-{device.node_safe}-cbw"
            if action == "disable":
                target = device.node
            await self._execute(
                "qom-set",
                arguments={
                    "path": device.qdev,
                    "property": "drive",
                    "value": target,
                },
            )

        return True

    async def prepare_target_devices(self, argv, devices, target_files):
        """Create the required target devices for blockev-backup
        operation"""
        self.log.info("Attach backup target devices to virtual machine")
        for device in devices:
            target = target_files[device.node]
            targetdev = f"qmpbackup-{device.node_safe}"

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

            await self._execute("blockdev-add", arguments=args)

    async def remove_snapshot_access_devices(self, devices):
        """Cleanup named devices after executing blockdev-backup
        operation"""
        self.log.info("Removing snapshot-access devices from virtual machine")
        for device in devices:
            targetdev = f"qmpbackup-{device.node_safe}-snap"

            await self._execute(
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
            targetdev = f"qmpbackup-{device.node_safe}-cbw"

            await self._execute(
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
            targetdev = f"qmpbackup-{device.node_safe}-fleece"

            await self._execute(
                "blockdev-del",
                arguments={
                    "node-name": targetdev,
                },
            )

    async def remove_target_devices(self, devices):
        """Cleanup named devices after executing blockdev-backup
        operation"""
        self.log.info("Removing backup target devices from virtual machine")
        for device in devices:
            targetdev = f"qmpbackup-{device.node_safe}"

            await self._execute(
                "blockdev-del",
                arguments={
                    "node-name": targetdev,
                },
            )

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
        if argv.no_persist is True:
            self.log.info("Create non-persistent bitmap.")
            persistent = False

        actions = []
        for device in devices:
            node = device.node
            if device.child_device is not None:
                node = device.child_device
            targetdev = f"qmpbackup-{device.node_safe}"
            bitmap = f"{bitmap_prefix}-{device.device}-{uuid}"
            job_id = f"qmpbackup.{device.node_safe}.{os.path.basename(device.filename)}"

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
                    self.transaction_bitmap_add(node, bitmap, persistent=persistent)
                )

            if device.has_bitmap and argv.level in ("full") and device.format != "raw":
                self.log.info(
                    "Clearing existing bitmap [%s] for device: [%s:%s]",
                    bitmap,
                    node,
                    os.path.basename(device.filename),
                )
                actions.append(self.transaction_bitmap_clear(node, bitmap))

            compress = argv.compress
            if device.format == "raw" and compress:
                compress = False
                self.log.info("Disabling compression for raw device: [%s]", device.node)

            if argv.no_fleece is False:
                snapshot_device = f"qmpbackup-{device.node_safe}-snap"
            else:
                snapshot_device = device.node

            if argv.level in ("full", "copy") or (
                argv.level == "inc" and device.format == "raw"
            ):
                actions.append(
                    self.transaction_action(
                        "blockdev-backup",
                        device=snapshot_device,
                        target=targetdev,
                        sync=sync,
                        job_id=job_id,
                        speed=argv.speed_limit,
                        compress=compress,
                        auto_dismiss=False,
                    )
                )
            else:
                if argv.no_fleece is False:
                    actions.append(self.transaction_bitmap_add(snapshot_device, bitmap))
                    bm_source = {
                        "name": bitmap,
                        "node": node,
                    }
                    merge_bitmap = {
                        "node": snapshot_device,
                        "target": bitmap,
                        "bitmaps": [bm_source],
                    }
                    actions.append(
                        self.transaction_action(
                            "block-dirty-bitmap-merge",
                            **merge_bitmap,
                        )
                    )
                actions.append(
                    self.transaction_action(
                        "blockdev-backup",
                        bitmap=bitmap,
                        device=snapshot_device,
                        target=targetdev,
                        sync=sync,
                        job_id=job_id,
                        speed=argv.speed_limit,
                        compress=argv.compress,
                        auto_dismiss=False,
                    )
                )
                actions.append(self.transaction_bitmap_clear(node, bitmap))

        self.log.debug("Created transaction: %s", json_pp(actions))

        return actions

    async def backup(self, argv, devices, qga, uuid):
        """Start backup transaction, while backup is active,
        watch for block status"""
        finished = 0
        actions = self.prepare_transaction(argv, devices, uuid)
        await self._execute("transaction", arguments={"actions": actions})
        if qga is not False:
            fs.thaw(qga)

        while True:
            if self.stop_backup is True:
                raise RuntimeError("Signal received")

            jobs = await self._execute("query-block-jobs")
            for job in jobs:
                if not job["type"] == "backup":
                    continue
                if not job["device"].startswith("qmpbackup"):
                    continue

                if job["status"] in ("aborting", "undefined"):
                    raise RuntimeError(
                        "Block job failed for device "
                        f"[{job['device']}]: [{job['status']}]"
                    )

                if job["status"] == "concluded" and job["offset"] != job["len"]:
                    raise RuntimeError(
                        "Block job cancelled during IO: "
                        f"[{job['device']}]: [{job['status']}]"
                        f"Offset:Len [{job['offset']}]: [{job['len']}]: [{job['error']}]"
                    )

                if job["status"] == "concluded" and job["offset"] == job["len"]:
                    await self._execute(
                        "block-job-dismiss", arguments={"id": job["device"]}
                    )
                    finished += 1
                    self.log.info("Block job [%s] finished", job["device"])
                    if len(devices) == finished:
                        self.log.info("All backups finished")
                        return

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
            sleep(argv.refresh_rate)

    async def do_query_block(self):
        """Return list of attached block devices"""
        return await self._execute("query-block")

    async def do_query_named_block(self):
        """Return list of attached block devices"""
        return await self._execute("query-named-block-nodes")

    async def remove_bitmaps(self, blockdev, prefix="qmpbackup", uuid=""):
        """Remove existing bitmaps for block devices"""
        for dev in blockdev:
            if not dev.has_bitmap:
                self.log.info("No bitmap set for device %s", dev.node)
                continue

            node = dev.node
            if dev.child_device is not None:
                node = dev.child_device

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
                await self._execute(
                    "block-dirty-bitmap-remove",
                    arguments={"node": node, "name": bitmap_name},
                )
