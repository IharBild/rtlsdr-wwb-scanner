#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "tuner_r82xx.h"
extern int n_wr, n_rd, lock_after_reads; extern unsigned char hw[256]; extern void reset_counts(void);
extern struct { char m[256]; char p[256]; } g_dev;
int main(int argc, char **argv) {
    static struct r82xx_config cfg; static struct r82xx_priv priv;
    cfg.i2c_addr = 0x74; cfg.xtal = 28800000; cfg.rafael_chip = CHIP_R828D; cfg.max_i2c_msg_len = 8;
    priv.cfg = &cfg; priv.rtl_dev = &g_dev;
    reset_counts();
    if (r82xx_init(&priv) < 0) { printf("init failed\n"); return 1; }
    printf("init: writes %d reads %d\n", n_wr, n_rd);
    if (argc > 2) lock_after_reads = atoi(argv[2]);
#ifdef HAVE_FAST
    priv.fast_tune = argc > 1 ? atoi(argv[1]) : 0;
#endif
    uint32_t f; int tiles = 0, tw = 0, tr = 0, fails = 0, unlocked = 0;
    for (f = 470000000; f <= 1000000000; f += 775000) {
        reset_counts();
        int rc = r82xx_set_freq(&priv, f);
        if (rc < 0) fails++;
        if (!priv.has_lock) unlocked++;
        tiles++; tw += n_wr; tr += n_rd;
    }
    unsigned h = 0; for (int k = 5; k < 32; k++) h = h * 31 + hw[k];
    printf("hw register hash %08x\n", h);
    printf("tiles %d: per tune writes %.1f reads %.1f usb transfers %.1f fails %d unlocked %d\n",
           tiles, (double)tw/tiles, (double)tr/tiles, (double)(tw + 2*tr)/tiles, fails, unlocked);
    return 0;
}
