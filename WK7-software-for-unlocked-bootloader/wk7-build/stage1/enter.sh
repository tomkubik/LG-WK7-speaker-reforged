#!/system/bin/sh
# Enter the Alpine chroot on top of LG's running Android (stage 1 test harness)
R=/data/wk7linux
mountpoint -q $R/proc || mount -t proc proc $R/proc
mountpoint -q $R/sys  || mount -o bind /sys $R/sys
mountpoint -q $R/dev  || mount -o bind /dev $R/dev
mountpoint -q $R/dev/pts || mount -t devpts devpts $R/dev/pts 2>/dev/null
mountpoint -q $R/tmp  || mount -t tmpfs tmpfs $R/tmp
mkdir -p $R/dev/shm; mountpoint -q $R/dev/shm || mount -t tmpfs -o mode=1777 tmpfs $R/dev/shm
exec chroot $R /usr/bin/env -i HOME=/root TERM=xterm PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /bin/sh "$@"
