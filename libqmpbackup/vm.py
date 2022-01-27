import logging
from collections import namedtuple

log = logging.getLogger(__name__)


class VMInfo:
    def get_block_devices(self, blockinfo):
        """Get a list of block devices that we can create a bitmap for,
        currently we only get inserted qcow based images
        """
        BlockDev = namedtuple(
            "BlockDev",
            ["node", "format", "filename", "backing_image", "has_bitmap", "bitmaps"],
        )
        blockdevs = []
        backing_image = False
        has_bitmap = False
        bitmaps = None
        for device in blockinfo:
            if not "inserted" in device:
                log.debug("Ignoring device: %s", device)
                continue

            inserted = device["inserted"]
            if inserted["drv"] == "raw":
                log.warning(
                    "Excluding device with raw format from backup: %s", device["device"]
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
                bi = inserted["image"]["backing-image"]
                backing_image = True
            except KeyError:
                pass

            log.debug("Adding device to device list: %s", device)
            blockdevs.append(
                BlockDev(
                    device["device"],
                    inserted["image"]["format"],
                    inserted["image"]["filename"],
                    backing_image,
                    has_bitmap,
                    bitmaps,
                )
            )

        if len(blockdevs) == 0:
            return None

        return blockdevs
