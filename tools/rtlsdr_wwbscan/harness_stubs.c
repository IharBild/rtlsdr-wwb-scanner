/* Fake RTL2832 side for counting tuner I2C traffic per retune. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
int n_wr = 0, n_rd = 0, n_rd_bytes = 0;
int lock_after_reads = 0;   /* PLL reports lock on the Nth status read (0 = immediately) */
static int status_reads = 0;
uint8_t hw[256];
struct fake_dev { char manufact[256]; char product[256]; };
struct fake_dev g_dev = {"RTLSDRBlog", "Blog V4"};
int rtlsdr_i2c_write_fn(void *dev, uint8_t addr, uint8_t *buf, int len) {
    n_wr++; memcpy(&hw[buf[0]], buf + 1, len - 1); return len; }
int rtlsdr_i2c_read_fn(void *dev, uint8_t addr, uint8_t *buf, int len) {
    n_rd++; n_rd_bytes += len; memset(buf, 0, len);
    /* r82xx reads from reg 0 and bit-reverses; status byte 2 bit 0x40 = lock.
       Raw value is bit-reversed by driver, so 0x40 after reversal = 0x02 raw. */
    if (len >= 3) { if (status_reads++ >= lock_after_reads) buf[2] = 0x02; }
    if (len >= 5) buf[4] = 0x0c; /* vco_fine_tune bits -> (0x30 after reverse) */
    return len; }
int rtlsdr_check_dongle_model(void *dev, char *m, char *p) {
    return strcmp(g_dev.manufact, m) == 0 && strcmp(g_dev.product, p) == 0; }
int rtlsdr_set_bias_tee_gpio(void *dev, int gpio, int on) { n_wr++; return 0; }
void reset_counts(void) { n_wr = n_rd = n_rd_bytes = 0; status_reads = 0; }
