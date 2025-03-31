

rm /tmp/fleece.qcow2 /tmp/backup.qcow2
qemu-img create -f qcow2 /tmp/fleece.qcow2 128G
qemu-img create -f qcow2 /tmp/backup.qcow2 128G

qmp-shell -vp /tmp/socket < start-full
sleep 5
qmp-shell -vp /tmp/socket < cleanup

python3 ../t/agent.py test1
python3 ../t/agent.py test2

rm /tmp/fleece.qcow2 /tmp/backup.qcow2
qemu-img create -f qcow2 /tmp/fleece.qcow2 128G
qemu-img create -f qcow2 /tmp/backup.qcow2 128G

exit

qmp-shell -vp /tmp/socket < start-inc
sleep 5
qmp-shell -vp /tmp/socket < cleanup

ls -alh /tmp/backup.qcow2


