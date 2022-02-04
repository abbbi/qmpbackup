import sys
from time import sleep

sys.path.append("..")
from libqmpbackup.qaclient import QemuGuestAgentClient


class TestCaseAgent(QemuGuestAgentClient):
    def create_dir_for_inc(self, target):
        self.qga.exec(path="/bin/cp", arg=["-r", "/etc", target])
        self.qga.exec(path="/bin/sync")


while True:
    try:
        qga = TestCaseAgent("/tmp/qga.sock")
    except:
        continue

    if not qga.ping(2):
        print("Waiting for VM to be reachable via guest agent")
        sleep(10)
        continue

    print("guest agent is reachable")
    break

cmd = None
try:
    cmd = sys.argv[1]
except:
    sys.exit(0)

if cmd is not None:
    print("Changing some files within guest")
    qga.create_dir_for_inc(f"/tmp/{cmd}")
