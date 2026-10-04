/* remount-root: remount Android's "/" (system-as-root, slot B) read-write or read-only via mount(2) directly.
 * Usage: remount-root rw|ro   (toybox/busybox mount can't, because "/" is listed as /dev/root twice) */
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>

int main(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "rw") && strcmp(argv[1], "ro"))) {
        fprintf(stderr, "usage: %s rw|ro\n", argv[0]);
        return 2;
    }
    unsigned long flags = MS_REMOUNT | (strcmp(argv[1], "ro") == 0 ? MS_RDONLY : 0);
    if (mount(NULL, "/", NULL, flags, NULL) != 0) {
        perror("mount");
        return 1;
    }
    printf("/ remounted %s\n", argv[1]);
    return 0;
}
