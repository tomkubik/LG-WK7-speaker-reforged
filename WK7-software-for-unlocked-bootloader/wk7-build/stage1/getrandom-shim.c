/* getrandom-shim: LG's 3.10 kernel lacks the getrandom() syscall (3.17+), which musl's getentropy()/getrandom()
 * rely on. Preload this to serve both from /dev/urandom instead. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sys/types.h>
#include <unistd.h>

ssize_t getrandom(void *buf, size_t len, unsigned flags)
{
    (void)flags;
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    size_t got = 0;
    while (got < len) {
        ssize_t n = read(fd, (char *)buf + got, len - got);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            close(fd);
            return got ? (ssize_t)got : -1;
        }
        got += n;
    }
    close(fd);
    return got;
}

int getentropy(void *buf, size_t len)
{
    if (len > 256) {
        errno = EIO;
        return -1;
    }
    return getrandom(buf, len, 0) == (ssize_t)len ? 0 : -1;
}
