qmp commands to do backup via cbw

 qmp-shell -vp /tmp/socket < start-full

-> wait for block job to finish, then cleanup:

 qmp-shell -vp /tmp/socket < cleanup-after-full

-> start incremental backup (fails)

kqmp-shell -vp /tmp/socket < cleanup-after-full
