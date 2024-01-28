"""
 qmpbackup: Full an incremental backup using Qemus
 dirty bitmap feature

 Copyright (C) 2022  Michael Ablassmeier

 Authors:
  Michael Ablassmeier <abi@grinser.de>

 This work is licensed under the terms of the GNU GPL, version 3.  See
 the LICENSE file in the top-level directory.
"""
import os
import logging
from collections import namedtuple

log = logging.getLogger(__name__)


def get_block_devices(blockinfo, excluded_disks, included_disks):
    """Get a list of block devices that we can create a bitmap for,
    currently we only get inserted qcow based images
    """
    BlockDev = namedtuple(
        "BlockDev",
        [
            "node",
            "format",
            "filename",
            "backing_image",
            "has_bitmap",
            "bitmaps",
            "virtual_size",
            "targetfile",
        ],
    )
    blockdevs = []
    for device in blockinfo:
        bitmaps = None
        has_bitmap = False
        backing_image = False
        if "inserted" not in device:
            log.debug("Ignoring non-inserted device: %s", device)
            continue

        inserted = device["inserted"]
        base_filename = os.path.basename(inserted["image"]["filename"])
        if inserted["drv"] == "raw":
            log.warning(
                "Excluding device with raw format from backup: [%s:%s]",
                device["device"],
                base_filename,
            )
            continue

        bitmaps = []
        if "dirty-bitmaps" in inserted:
            bitmaps = inserted["dirty-bitmaps"]

        if "dirty-bitmaps" in device:
            bitmaps = device["dirty-bitmaps"]

        if len(bitmaps) > 0:
            has_bitmap = True

        try:
            backing_image = inserted["image"]["backing-image"]
            backing_image = True
        except KeyError:
            pass

        if included_disks and not device["device"] in included_disks:
            log.info(
                "Device not in included disk list, ignoring: [%s:%s]",
                device["device"],
                base_filename,
            )
            continue

        if excluded_disks and device["device"] in excluded_disks:
            logging.info(
                "Excluding device from backup: [%s:%s]",
                device["device"],
                base_filename,
            )
            continue

        log.debug("Adding device to device list: %s", device)
        blockdevs.append(
            BlockDev(
                device["device"],
                inserted["image"]["format"],
                inserted["image"]["filename"],
                backing_image,
                has_bitmap,
                bitmaps,
                inserted["image"]["virtual-size"],
                None,
            )
        )

    if len(blockdevs) == 0:
        return None

    return blockdevs
