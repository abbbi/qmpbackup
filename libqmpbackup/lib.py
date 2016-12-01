import os
import sys
from json import dumps as json_dumps
import logging
import subprocess

class QmpBackup:
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

    def rebase(self, directory):
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
            if images.index(image) == 0:
                break;

            self._log.debug('"%s" is based on "%s"' % (
                images[idx],
                image
            ))

            # befor rebase we check consistency of all files
            CMD_CHECK = 'qemu-img check %s' % image
            try:
                self._log.info(CMD_CHECK)
                output = subprocess.check_output(CMD_CHECK, shell=True)
            except subprocess.CalledProcessError as e:
                self._log.error('Error while file check: %s' % e)
                return False

            try:
                CMD_REBASE = 'qemu-img rebase -b "%s" "%s" -u' % (
                    images[idx],
                    image,
                )

                reb = subprocess.check_output(CMD_REBASE, shell=True)
                self._log.info(CMD_REBASE)
                CMD_COMMIT = 'qemu-img commit "%s"' % image
                self._log.info(CMD_COMMIT)
                com = subprocess.check_output(CMD_COMMIT, shell=True)
            except subprocess.CalledProcessError as e:
                self._log.error('Error while rollback: %s' % e)
                return False

        return True
