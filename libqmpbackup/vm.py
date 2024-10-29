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
import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class BlockDev:
    """Block device information"""

    node: str
    format: str
    filename: str
    backing_image: str
    has_bitmap: bool
    bitmaps: list
    virtual_size: int
    driver: str


def get_block_devices(blockinfo, argv, excluded_disks, included_disks, uuid):
    """Get a list of block devices that we can create a bitmap for,
    currently we only get inserted qcow based images
    """
    blockdevs = []
    for device in blockinfo:
        bitmaps = None
        has_bitmap = False
        backing_image = False
        driver = None
        if "inserted" not in device:
            log.debug("Ignoring non-inserted device: %s", device)
            continue

        inserted = device["inserted"]
        if (
            inserted["drv"] == "raw"
            and not argv.include_raw
            and not device["device"].startswith("pflash")
        ):
            log.warning(
                "Excluding device with raw format from backup: [%s:%s]",
                device["device"],
                inserted["image"]["filename"],
            )
            continue

        bitmaps = []
        if "dirty-bitmaps" in inserted:
            bitmaps = inserted["dirty-bitmaps"]

        if "dirty-bitmaps" in device:
            bitmaps = device["dirty-bitmaps"]

        if len(bitmaps) > 0 and uuid is not None:
            for bmap in bitmaps:
                if bmap["name"].endswith(uuid):
                    has_bitmap = True
                    break
        else:
            if len(bitmaps) > 0:
                has_bitmap = True

        try:
            backing_image = inserted["image"]["backing-image"]
            backing_image = True
            filename = inserted["image"]["backing-image"]["filename"]
            diskformat = inserted["image"]["backing-image"]["format"]
        except KeyError:
            filename = inserted["image"]["filename"]
            diskformat = inserted["image"]["format"]

        if filename.startswith("json:"):
            log.debug("Filename setting is json encoded..")
            try:
                encoded_name = json.loads(filename[5:])
                try:
                    log.debug("Check if device is an RBD backed device.")
                    driver = encoded_name["file"]["driver"]
                    if driver == "rbd":
                        log.info("Ceph device found, using image name")
                        filename = encoded_name["file"]["image"]
                except KeyError:
                    log.debug("Non RBD Device detected, use filename setting.")
                    try:
                        filename = encoded_name["file"]["next"]["filename"]
                    except KeyError:
                        log.warning(
                            "Json encoded setting found but no filename property set for device: [%s]",
                            device["device"],
                        )
                        continue
            except json.decoder.JSONDecodeError as errmsg:
                log.warning(
                    "Unable to decode filename json for device [%s]: %s",
                    errmsg,
                    device["device"],
                )
                continue

        if included_disks and not device["device"] in included_disks:
            log.info(
                "Device not in included disk list, ignoring: [%s:%s]",
                device["device"],
                filename,
            )
            continue

        if excluded_disks and device["device"] in excluded_disks:
            logging.info(
                "Excluding device from backup: [%s:%s]",
                device["device"],
                filename,
            )
            continue

        log.debug("Adding device to device list: %s", device)
        blockdevs.append(
            BlockDev(
                device["device"],
                diskformat,
                filename,
                backing_image,
                has_bitmap,
                bitmaps,
                inserted["image"]["virtual-size"],
                driver,
            )
        )

    if len(blockdevs) == 0:
        return None

    return blockdevs
