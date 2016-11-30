class BlockDev:
    def __init__(self, node, format, filename, backing_image, has_bitmap, bitmaps):
        ''' Represent block device informatoin
        '''
        self.node   = node
        self.format = format
        self.filename = filename
        self.backing_image = backing_image
        self.has_bitmap = has_bitmap
        self.bitmaps = bitmaps

class VMInfo:
    def get_block_devices(self, blockinfo):
        ''' Get a list of block devices that we can create a bitmap for,
            currently we only get inserted qcow based images
        '''
        blockdevs = []
        backing_image = False
        has_bitmap = False
        bitmaps = None
        for device in blockinfo:
            try:
                inserted = device['inserted']
                if inserted['drv'] == 'raw':
                    continue

                try:
                    if len(device['dirty-bitmaps']) > 0:
                        has_bitmap = True
                        bitmaps = device['dirty-bitmaps']
                except KeyError:
                    pass

                try:
                    bi =  inserted['image']['backing-image']
                    backing_image = True
                except KeyError:
                    pass

                blockdevs.append(BlockDev(
                     device['device'],
                     inserted['image']['format'],
                     inserted['image']['filename'],
                     backing_image,
                     has_bitmap,
                     bitmaps
                     ))
            except KeyError:
                 continue

        if len(blockdevs) == 0:
            return None

        return blockdevs
