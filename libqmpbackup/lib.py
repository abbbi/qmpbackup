import os
import sys
from json import dumps as json_dumps
from qaclient import QemuGuestAgentClient
import logging
import subprocess

class QmpBackup():
    ''' common functions '''
    def __init__(self, debug):
        self._log = self.setup_log(debug)

    def setup_log(self, debug):
        ''' setup logging '''
        FORMAT = '%(asctime)-15s %(levelname)5s  %(message)s'
        if debug:
            loglevel=logging.DEBUG
        else:
            loglevel=logging.INFO
        logging.basicConfig(format=FORMAT, level=loglevel)
        return logging.getLogger(sys.argv[0])

    def json_pp(self, json):
        ''' human readable json output '''
        try:
            return json_dumps(json, indent=4, sort_keys=True)
        except Exception as e:
            raise

    def rebase(self, directory, dry_run):
        ''' Rebase and commit all images in a directory '''
        if not os.path.exists(directory):
            self._log.error('Unable to find target directory')
            return False

        os.chdir(directory)
        images = filter(
            os.path.isfile,
            os.listdir(directory)
        )
        images = [
            os.path.join(directory, f)
            for f in images
        ]
        # sort files by creation date
        images.sort(key=lambda x: os.path.getmtime(x))

        if len(images) == 0:
            self._log.error('No image files found in specified directory')
            return False

        if not "FULL-" in images[0]:
            self._log.error('First image file is not a FULL base image')
            return False

        if "FULL-" in images[-1]:
            self._log.error('No incremental images found, nothing to commit')
            return False

        idx = len(images)-1
        for image in reversed(images):
            idx=idx-1
            if images.index(image) == 0 or 'FULL-' in images[images.index(image)]:
                self._log.info('Rollback of latest [FULL]<-[INC] chain complete, ignoring older chains')
                break;

            self._log.debug('"%s" is based on "%s"' % (
                images[idx],
                image
            ))

            # befor rebase we check consistency of all files
            CMD_CHECK = 'qemu-img check %s' % image
            try:
                self._log.info(CMD_CHECK)
                if not dry_run:
                    output = subprocess.check_output(CMD_CHECK, shell=True)
            except subprocess.CalledProcessError as e:
                self._log.error('Error while file check: %s' % e)
                return False

            try:
                CMD_REBASE = 'qemu-img rebase -b "%s" "%s" -u' % (
                    images[idx],
                    image,
                )

                if not dry_run:
                    reb = subprocess.check_output(CMD_REBASE, shell=True)
                self._log.info(CMD_REBASE)
                CMD_COMMIT = 'qemu-img commit "%s"' % image
                self._log.info(CMD_COMMIT)
                if not dry_run:
                    com = subprocess.check_output(CMD_COMMIT, shell=True)
            except subprocess.CalledProcessError as e:
                self._log.error('Error while rollback: %s' % e)
                return False

        return True

    def check_bitmap_state(self, node, bitmaps):
        ''' Check if the bitmap state is ready for backup

            active  -> Ready for backup
            frozen  -> backup in progress
            disabled-> migration might be going on
        '''
        for bitmap in bitmaps:
            self._log.debug('Bitmap: %s' % self.json_pp(bitmap))
            match = "%s-%s" % ('qmpbackup', node)
            if bitmap['name'] == match and bitmap['status'] == "active":
                return True

        return bitmap['status']

    def connect_qaagent(self, socket):
        ''' Setup Qemu Agent connection '''
        try:
            qga = QemuGuestAgentClient(socket)
            self._log.info('Guest Agent socket connected')
        except QemuGuestAgentClient.error as e:
            self._log.warning('Unable to connect qemu guest agent socket: "%s"' % e)
            return False

        if not qga.ping(5):
            self._log.warning('Unable to reach Qemu Guest Agent')
            return False
        else:
            qga_info = qga.info()
            self._log.info('Qemu Guest Agent is reachable')
            if not 'guest-fsfreeze-freeze' in qga_info:
                self._log.warning('Guest agent does not support needed commands')
    
        return qga

    def quisce(self, qga):
        ''' Quisce VM filesystem '''
        fsstate = self.fsgetstate(qga)
        if fsstate == "frozen":
            self._log.warning('Filesystem is already frozen')
            return True
        
        try:
            reply = qga.fsfreeze('freeze')
            self._log.info('"%s" Filesystem(s) freezed' % reply)
            return reply
        except Exception as e:
            self._log.warning('Unable to freeze: "%s"' % e)

        return None

    def thaw(self, qga):
        ''' Thaw filesystems '''
        fsstate = self.fsgetstate(qga)
        if fsstate == "thawed":
            self._log.info('Filesystem is already thawed, skipping.')
            return True
        try:
            reply = qga.fsfreeze('thaw')
            self._log.info('"%s" fileystem(s) thawed' % reply)
            return reply
        except Exception as e:
            self._log.warning('Unable to thaw filesystem: "%s"' % e)

        return None

    def fsgetstate(self, qga):
        ''' Return filesystem state '''
        try:
            reply = qga.fsfreeze('status')
            return reply
        except Exception as e:
            self._log.warning('Unable to get Filesytem status')

        return None
