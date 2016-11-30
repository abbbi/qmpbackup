class QmpBackup:
    ''' common functions '''
    def __init__(self, qmp, log):
        self.qmp = qmp
        self.log = log

    def remove_bitmaps(self, blockdev):
        ''' Loop through existing devices and bitmaps, remove them '''
        for dev in blockdev:
            if dev.has_bitmap:
                try:
                    for bitmap in dev.bitmaps:
                        if qmp.remove_bitmap(dev.node, bitmap['name']):
                            self.log.debug('Bitmap "%s" for device "%s" removed' % (
                                dev.node,
                                bitmap['name']
                            ))
                    return True
                except Exception as e:
                    raise
            else:
                self.log.debug('No bitmap set for any device')
        return False
