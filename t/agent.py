import sys
from time import sleep

sys.path.append("..")
from libqmpbackup.qaclient import QemuGuestAgentClient

while True:
    try:
        qga = QemuGuestAgentClient("/tmp/qga.sock")
    except:
        continue

    if not qga.ping(2):
        print("Waiting for VM to be reachable via guest agent")
        sleep(10)
        continue

    break

cmd = None
try:
    cmd = sys.argv[1]
except:
    sys.exit(0)

if cmd is not None:
    print("Changing some files within guest")
    qga._create_dir_for_inc(f"/tmp/{cmd}")
