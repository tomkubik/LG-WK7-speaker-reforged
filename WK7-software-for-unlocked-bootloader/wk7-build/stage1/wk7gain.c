/* wk7gain [volume_file [default]]: S16_LE stereo stdin -> stdout with a speaker-side volume (0..100) read from
 * volume_file (default /run/wk7.volume, default level 40).
 * VOLUME POLICY: two separate levels.
 *  - /run/wk7.volume: the moOde stream level (Multiroom and HTTP stream), independent of moOde's own knob.
 *    Only the dashboard slider and the speaker buttons (when AirPlay is not playing) write it.
 *  - /run/wk7.airplay-volume: AirPlay trim, used as "wk7gain /run/wk7.airplay-volume 100" by player.sh.
 *    The phone/Mac AirPlay volume is the real control; this is reset to 100 (no change) at every AirPlay
 *    session start and only the speaker buttons nudge it down/up during AirPlay. The slider never touches it.
 *  - /run/wk7.spotify-volume: the same kind of trim for Spotify Connect (wk7-spotify-player); the Spotify app's
 *    volume is the real control (spotifyd soft-volume), reset to 100 when Spotify starts playing.
 * (An ALSA softvol control can't be used: on this kernel it is locked to its creator.) */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const char *volume_file = "/run/wk7.volume";
static int default_level = 40;
#define FRAMES 1024
#define MIN_DB -50.0

static double read_gain(void)
{
    FILE *f = fopen(volume_file, "r");
    int v = default_level;          /* if the volume file is missing */
    if (f) {
        if (fscanf(f, "%d", &v) != 1)
            v = default_level;
        fclose(f);
    }
    if (v <= 0)
        return 0.0;
    if (v >= 100)
        return 1.0;
    return pow(10.0, (MIN_DB * (1.0 - v / 100.0)) / 20.0);   /* linear-in-dB taper */
}

int main(int argc, char **argv)
{
    int16_t buf[FRAMES * 2];
    if (argc > 1)
        volume_file = argv[1];
    if (argc > 2)
        default_level = atoi(argv[2]);
    double gain = read_gain();
    time_t last_check = 0;
    struct stat st;
    time_t last_mtime = 0;
    size_t n;

    while ((n = fread(buf, sizeof(int16_t), FRAMES * 2, stdin)) > 0) {
        time_t now = time(NULL);
        if (now != last_check) {                         /* re-read at most once per second, or on change */
            last_check = now;
            if (stat(volume_file, &st) == 0 && st.st_mtime != last_mtime) {
                last_mtime = st.st_mtime;
                gain = read_gain();
            }
        }
        if (gain < 1.0)
            for (size_t i = 0; i < n; i++)
                buf[i] = (int16_t)lrint(buf[i] * gain);
        if (fwrite(buf, sizeof(int16_t), n, stdout) != n)
            return 1;
        fflush(stdout);
    }
    return 0;
}
