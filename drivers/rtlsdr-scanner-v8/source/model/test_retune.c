#include <stdio.h>
#include <math.h>
#include "../VARIANT/src/librtlsdr.c"

extern int mock_ctrl_in, mock_ctrl_out, mock_tuner_wr, mock_tuner_rd, mock_pll_mode, mock_fail_writes, mock_fail_reg;
extern uint8_t mock_tregs[32];
extern int mock_i2c_no_rep;
extern int mock_repeater, mock_strict_rep;
extern unsigned char mock_sys[256];
extern int mock_gpi_ext;
extern int mock_fail_reg_reset, mock_fail_reads, mock_reset_on_fail, mock_tuner_resets, mock_no_device, mock_link_io, mock_bulk_hang, mock_bulk_calls;
extern unsigned int mock_last_bulk_timeout;

/* scanner-v6: tuner registers 0x05..0x1f that differ between the chip and the
 * driver's shadow copy (0x20..0x22 are not modelled by the fake chip) */
static int reg_mismatch(rtlsdr_dev_t *dev, int *first)
{
	int n = 0;
	*first = -1;
	for (int r = 0x05; r <= 0x1f; r++)
		if (mock_tregs[r] != dev->r82xx_p.regs[r - 0x05]) { if (*first < 0) *first = r; n++; }
	return n;
}

/* LO actually programmed into the chip, from the emulated register file */
static double programmed_lo(uint32_t xtal)
{
	int div_num = (mock_tregs[0x10] >> 5) & 7;
	int ni = mock_tregs[0x14] & 0x3f, si = mock_tregs[0x14] >> 6;
	int nint = 4 * ni + si + 13;
	int sdm = (mock_tregs[0x16] << 8) | mock_tregs[0x15];
	int mix_div = 1 << (div_num + 1);
	return 2.0 * xtal * (nint + sdm / 65536.0) / mix_div;
}

static void sweep(rtlsdr_dev_t *dev, double f0, double f1, double step, const char *name)
{
	int n = 0, tot = 0, mx = 0, tw = 0, trd = 0, errs = 0;
	double maxerr = 0;
	for (double f = f0; f <= f1 + 1; f += step) {
		int a = mock_ctrl_in + mock_ctrl_out, w = mock_tuner_wr, r = mock_tuner_rd;
		if (rtlsdr_set_center_freq(dev, (uint32_t)f)) errs++;
		int d = mock_ctrl_in + mock_ctrl_out - a;
		tot += d; if (d > mx) mx = d; tw += mock_tuner_wr - w; trd += mock_tuner_rd - r; n++;
		double lo_req = (double)(uint32_t)f + dev->r82xx_p.int_freq;
		double e = fabs(programmed_lo(dev->r82xx_c.xtal) - lo_req);
		if (e > maxerr) maxerr = e;
	}
	printf("%-10s tiles %4d | USB ctrl per retune avg %.1f max %d | tuner I2C wr %.1f rd %.1f | set_freq errors %d | max LO error %.1f Hz\n",
	       name, n, (double)tot / n, mx, (double)tw / n, (double)trd / n, errs, maxerr);
}

int main(int argc, char **argv)
{
	rtlsdr_dev_t *dev;
	if (rtlsdr_open(&dev, 0)) { printf("open failed\n"); return 1; }
	rtlsdr_set_sample_rate(dev, 2400000);
	rtlsdr_set_tuner_gain_mode(dev, 1);
	rtlsdr_set_tuner_gain(dev, 338);
	printf("tuner %d xtal %u int_freq %u\n", dev->tuner_type, dev->r82xx_c.xtal, dev->r82xx_p.int_freq);
	rtlsdr_set_center_freq(dev, 469000000);
	sweep(dev, 470e6, 800e6, 775000, "470-800");
	rtlsdr_set_center_freq(dev, 30000000);
	sweep(dev, 30e6, 1766e6, 775000, "30-1766");

#ifdef SCANNER_V4
	uint32_t late = 0, unl = 0; int lk;
	printf("build: %s\n", rtlsdr_get_build_info());
	mock_pll_mode = 2;  /* PLL needs extra time */
	rtlsdr_set_center_freq(dev, 600000000);
	lk = rtlsdr_get_tuner_pll_lock(dev, &late, &unl);
	printf("PLL late lock  : lock=%d late_locks=%u unlocks=%u\n", lk, late, unl);
	mock_pll_mode = 1;  /* PLL never locks */
	int r = rtlsdr_set_center_freq(dev, 601000000);
	lk = rtlsdr_get_tuner_pll_lock(dev, &late, &unl);
	printf("PLL no lock    : set_center_freq=%d lock=%d late_locks=%u unlocks=%u\n", r, lk, late, unl);
	mock_pll_mode = 0;
	rtlsdr_set_center_freq(dev, 602000000);
	printf("PLL recovered  : lock=%d\n", rtlsdr_get_tuner_pll_lock(dev, NULL, NULL));
#endif
	/* T1: the PLL register write fails, then the same frequency is retried */
	rtlsdr_set_center_freq(dev, 650000000);
	mock_fail_reg = 0x10;
	int r1 = rtlsdr_set_center_freq(dev, 700000000);
	int r2 = rtlsdr_set_center_freq(dev, 700000000);
	double want = 700000000.0 + dev->r82xx_p.int_freq;
	printf("T1 PLL write fails : 1st %d, retry %d, LO error after retry %.0f Hz\n", r1, r2, fabs(programmed_lo(dev->r82xx_c.xtal) - want));
	/* T2: VHF -> UHF input switch write fails, then retried */
	rtlsdr_set_center_freq(dev, 200000000);
	mock_fail_reg = 0x05;
	r1 = rtlsdr_set_center_freq(dev, 600000000);
	r2 = rtlsdr_set_center_freq(dev, 600000000);
	printf("T2 input switch fails: 1st %d, retry %d, reg05 cable1=%d air_in_off=%d (want 0 0 = UHF input)\n",
	       r1, r2, !!(mock_tregs[0x05] & 0x40), !!(mock_tregs[0x05] & 0x20));
	/* other API calls between retunes must not leave the repeater cache stale */
	rtlsdr_set_sample_rate(dev, 2048000);  rtlsdr_set_center_freq(dev, 610000000);
	rtlsdr_set_tuner_gain(dev, 200);       rtlsdr_set_center_freq(dev, 620000000);
	rtlsdr_set_tuner_bandwidth(dev, 0);    rtlsdr_set_center_freq(dev, 630000000);
	rtlsdr_set_freq_correction(dev, 2);    rtlsdr_set_center_freq(dev, 640000000);
	rtlsdr_set_agc_mode(dev, 0);           rtlsdr_set_center_freq(dev, 150000000);
	rtlsdr_set_tuner_gain_mode(dev, 0);    rtlsdr_set_center_freq(dev, 20000000);
	rtlsdr_reset_buffer(dev);              rtlsdr_set_center_freq(dev, 1700000000);
	rtlsdr_set_sample_rate(dev, 2400000);  rtlsdr_set_center_freq(dev, 470000000);
	printf("tuner I2C transfers while the repeater was off: %d\n", mock_i2c_no_rep);
#ifdef SCANNER_V5
	{
		uint32_t rt0, fl0, rt, fl;
		int ra;
		rtlsdr_get_i2c_stats(dev, &rt0, &fl0);
		/* T3: two tuner writes in a row fail (PIPE), the retune still works */
		rtlsdr_set_center_freq(dev, 500000000);
		mock_fail_writes = 2;
		ra = rtlsdr_set_center_freq(dev, 555000000);
		want = 555000000.0 + dev->r82xx_p.int_freq;
		rtlsdr_get_i2c_stats(dev, &rt, &fl);
		printf("T3 2 writes fail    : set_center_freq %d, LO error %.0f Hz, retries %u, failures %u (want 0, <300, 2, 0)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want), rt - rt0, fl - fl0);
		/* T4: the tuner stops answering for good: an error comes back after the retries */
		mock_fail_writes = 1000;
		ra = rtlsdr_set_center_freq(dev, 777000000);
		rtlsdr_get_i2c_stats(dev, &rt0, &fl0);
		printf("T4 tuner silent     : set_center_freq %d (want <0), failures %u (want >= 1)\n", ra, fl0 - fl);
		mock_fail_writes = 0;
		ra = rtlsdr_set_center_freq(dev, 777000000);
		want = 777000000.0 + dev->r82xx_p.int_freq;
		printf("T4 tuner back       : set_center_freq %d, LO error %.0f Hz (want 0, <300)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want));
		/* T5: the repeater went off behind the driver's back (cache says on) */
		mock_strict_rep = 1;
		rtlsdr_set_center_freq(dev, 600000000);
		mock_repeater = 0;
		dev->i2c_repeater_state = 1;
		rtlsdr_get_i2c_stats(dev, &rt0, &fl0);
		ra = rtlsdr_set_tuner_gain(dev, 496);
		int rb = rtlsdr_set_center_freq(dev, 640000000);
		rtlsdr_get_i2c_stats(dev, &rt, &fl);
		printf("T5 repeater lost    : set_tuner_gain %d, set_center_freq %d, retries %u, failures %u (want 0, 0, >=1, 0)\n",
		       ra, rb, rt - rt0, fl - fl0);
		mock_strict_rep = 0;
	}
#endif

	{
		/* ---- scanner-v6 (also run on v5 to show the difference) ---- */
		int ra, first, mism, a, w;
		uint32_t rt0, fl0, rt, fl;
		double want;
		mock_strict_rep = 0;
		rtlsdr_set_tuner_gain_mode(dev, 1);
		rtlsdr_set_tuner_gain(dev, 338);
		rtlsdr_set_center_freq(dev, 600000000);
		mism = reg_mismatch(dev, &first);
		printf("T6 before           : registers chip != copy %d (want 0)\n", mism);
		/* T6: a gain write needs one retry, and the failure was a tuner reset */
		mock_reset_on_fail = 1; mock_fail_writes = 1;
#ifdef SCANNER_V6
		uint32_t rs0 = 0; rtlsdr_get_tuner_restores(dev, &rs0);
#endif
		rtlsdr_get_i2c_stats(dev, &rt0, &fl0);
		ra = rtlsdr_set_tuner_gain(dev, 496);
		rtlsdr_get_i2c_stats(dev, &rt, &fl);
		mism = reg_mismatch(dev, &first);
		printf("T6 tuner reset      : set_tuner_gain %d, resets %d, retries %u, registers chip != copy %d (first 0x%02x) (want 0, 1, >=1, 0)\n",
		       ra, mock_tuner_resets, rt - rt0, mism, first < 0 ? 0 : first);
		ra = rtlsdr_set_center_freq(dev, 610000000);
		want = 610000000.0 + dev->r82xx_p.int_freq;
		mism = reg_mismatch(dev, &first);
		printf("T6 next retune      : set_center_freq %d, LO error %.0f Hz, registers chip != copy %d, PLL lock %d (want 0, <300, 0, 1)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want), mism, rtlsdr_get_tuner_pll_lock(dev, NULL, NULL));
#ifdef SCANNER_V6
		{ uint32_t rs = 0; rtlsdr_get_tuner_restores(dev, &rs); printf("T6 restores         : %u (want 1)\n", rs - rs0); }
#endif
		/* T7: a write fails for good, then the tuner answers again:
		 * all registers rewritten once, skipping redundant writes works again */
		mock_fail_writes = 1000;
		ra = rtlsdr_set_tuner_gain(dev, 338);
		mock_fail_writes = 0;
		int rb = rtlsdr_set_center_freq(dev, 620000000);
		mism = reg_mismatch(dev, &first);
		printf("T7 failure, recovery: set_tuner_gain %d, set_center_freq %d, registers chip != copy %d, shadow_dirty %d (want <0, 0, 0, 0)\n",
		       ra, rb, mism, dev->r82xx_p.shadow_dirty);
		sweep(dev, 630e6, 800e6, 775000, "T7 after");
		/* T11: the PLL status read needs a retry and the tuner was reset */
		mock_reset_on_fail = 1; mock_fail_reads = 1;
		ra = rtlsdr_set_center_freq(dev, 640000000);
		want = 640000000.0 + dev->r82xx_p.int_freq;
		mism = reg_mismatch(dev, &first);
		printf("T11 read retry+reset: set_center_freq %d, LO error %.0f Hz, registers chip != copy %d, PLL lock %d (want 0, <300, 0, 1)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want), mism, rtlsdr_get_tuner_pll_lock(dev, NULL, NULL));
		/* T12: a second reset in the middle of the rewrite itself (chunk at 0x13) */
		{
			int res0 = mock_tuner_resets;
			mock_reset_on_fail = 1; mock_fail_writes = 1;
			mock_fail_reg = 0x13; mock_fail_reg_reset = 1;
			ra = rtlsdr_set_tuner_gain(dev, 300);
			mism = reg_mismatch(dev, &first);
			printf("T12 reset in rewrite: set_tuner_gain %d, tuner resets %d, registers chip != copy %d, shadow_dirty %d (want 0, 2, 0, 0)\n",
			       ra, mock_tuner_resets - res0, mism, dev->r82xx_p.shadow_dirty);
			mock_fail_reg = -1; mock_fail_reg_reset = 0;
		}
		/* T13: after a restore the PLL does not lock: reported, not hidden */
		mock_pll_mode = 1; mock_reset_on_fail = 1; mock_fail_writes = 1;
		ra = rtlsdr_set_tuner_gain(dev, 338);
		printf("T13 no lock after   : set_tuner_gain %d, PLL lock %d (want 0, 0)\n", ra, rtlsdr_get_tuner_pll_lock(dev, NULL, NULL));
		mock_pll_mode = 0;
		ra = rtlsdr_set_center_freq(dev, 645000000);
		printf("T13 next retune     : set_center_freq %d, PLL lock %d (want 0, 1)\n", ra, rtlsdr_get_tuner_pll_lock(dev, NULL, NULL));
		/* T9: the link is lost the Windows way (every control transfer IO error) */
		w = mock_tuner_wr; a = mock_ctrl_in + mock_ctrl_out;
		mock_link_io = 1;
		ra = rtlsdr_set_center_freq(dev, 700000000);
		mock_link_io = 0;
		printf("T9 link lost (IO)   : set_center_freq %d, tuner I2C writes tried %d, USB ctrl %d (v5: 4 tries per write)\n",
		       ra, mock_tuner_wr - w, mock_ctrl_in + mock_ctrl_out - a);
		ra = rtlsdr_set_center_freq(dev, 705000000);
		want = 705000000.0 + dev->r82xx_p.int_freq;
		mism = reg_mismatch(dev, &first);
		printf("T9 link back        : set_center_freq %d, LO error %.0f Hz, registers chip != copy %d (want 0, <300, 0)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want), mism);
		/* T10: the bulk endpoint stops delivering samples */
		{
			static unsigned char buf[163840];
			int n = -1;
			rtlsdr_set_sample_rate(dev, 2400000);
			mock_bulk_hang = 1;
			ra = rtlsdr_read_sync(dev, buf, sizeof(buf), &n);
			printf("T10 no samples      : read_sync %d, timeout passed %u ms (want -7, ~2068; v5: -99 = blocks for ever, 0 ms)\n",
			       ra, mock_last_bulk_timeout);
#ifdef SCANNER_V6
			rtlsdr_set_sync_read_timeout(dev, 0);
			ra = rtlsdr_read_sync(dev, buf, sizeof(buf), &n);
			printf("T10 timeout 0       : read_sync %d, timeout %u ms (want -99, 0 = upstream)\n", ra, mock_last_bulk_timeout);
			rtlsdr_set_sync_read_timeout(dev, 1500);
			ra = rtlsdr_read_sync(dev, buf, sizeof(buf), &n);
			printf("T10 timeout 1500    : read_sync %d, timeout %u ms (want -7, 1500)\n", ra, mock_last_bulk_timeout);
			rtlsdr_set_sync_read_timeout(dev, -1);
			rtlsdr_set_sample_rate(dev, 250000);
			ra = rtlsdr_read_sync(dev, buf, sizeof(buf), &n);
			printf("T10 250 kS/s        : timeout %u ms (want 2655)\n", mock_last_bulk_timeout);
#endif
			mock_bulk_hang = 0;
			rtlsdr_set_sample_rate(dev, 2400000);
			ra = rtlsdr_read_sync(dev, buf, sizeof(buf), &n);
			printf("T10 samples back    : read_sync %d, %d bytes (want 0, 163840)\n", ra, n);
		}
#ifdef SCANNER_V7
		/* T14: dithering (pyrtlsdr >= 0.4 calls it with 1 in open()) */
		{
			int c0 = mock_ctrl_in + mock_ctrl_out, d1, d0, d2;
			ra = rtlsdr_set_dithering(dev, 1);
			int c1 = mock_ctrl_in + mock_ctrl_out;
			rtlsdr_set_center_freq(dev, 650000000);
			d1 = mock_tregs[0x12] & 0x10;
			rb = rtlsdr_set_dithering(dev, 0);
			rtlsdr_set_center_freq(dev, 651000000);
			d0 = mock_tregs[0x12] & 0x10;
			want = 651000000.0 + dev->r82xx_p.int_freq;
			double e0 = fabs(programmed_lo(dev->r82xx_c.xtal) - want);
			rtlsdr_set_dithering(dev, 1);
			rtlsdr_set_center_freq(dev, 652000000);
			d2 = mock_tregs[0x12] & 0x10;
			mism = reg_mismatch(dev, &first);
			printf("T14 dithering       : set(1) %d with %d USB transfers, reg 0x12 bit4 on/off/on %d/%d/%d, LO error off %.0f Hz, chip != copy %d, set(NULL) %d (want 0, 0, 0/16/0, <300, 0, -1)\n",
			       ra, c1 - c0, d1, d0, d2, e0, mism, rtlsdr_set_dithering(NULL, 1));
			(void)rb;
		}
		/* T15: GPIO API of the librtlsdr fork */
		{
			int v = -1, st = -1, by = -1, r1, r2, r3, r4, r5, r6;
			memset(mock_sys, 0, sizeof(mock_sys));
			r1 = rtlsdr_set_gpio_output(dev, 3);
			r2 = rtlsdr_set_gpio_bit(dev, 3, 1);
			r3 = rtlsdr_get_gpio_bit(dev, 3, &v);
			int gpo = mock_sys[1], gpoe = mock_sys[3], gpd = mock_sys[4];
			r4 = rtlsdr_set_gpio_input(dev, 2);
			mock_gpi_ext = 0x04;
			int vin = -1; rtlsdr_get_gpio_bit(dev, 2, &vin);
			r5 = rtlsdr_set_gpio_status(dev, &st);
			r6 = rtlsdr_get_gpio_byte(dev, &by);
			rtlsdr_set_gpio_byte(dev, 0x00);
			printf("T15 GPIO            : rc %d %d %d %d %d %d; out pin 3: GPO %02x GPOE %02x GPD %02x read %d; in pin 2: %d, status(GPD) %02x, byte %02x; bad pin %d, NULL %d (want 0s, 08 08 00 1, 1, 04, 0c)\n",
			       r1, r2, r3, r4, r5, r6, gpo, gpoe, gpd, v, vin, st, by,
			       rtlsdr_set_gpio_bit(dev, 8, 1), rtlsdr_get_gpio_bit(dev, 1, NULL));
			mock_gpi_ext = 0;
		}
#endif
		/* T8: dongle unplugged, then the program closes it (r82xx_standby: 11 writes) */
		w = mock_tuner_wr; a = mock_ctrl_in + mock_ctrl_out;
		mock_no_device = 1;
		ra = rtlsdr_set_center_freq(dev, 710000000);
		printf("T8 unplugged retune : set_center_freq %d, tuner I2C writes tried %d\n", ra, mock_tuner_wr - w);
		w = mock_tuner_wr; a = mock_ctrl_in + mock_ctrl_out;
		rtlsdr_close(dev);
		printf("T8 close unplugged  : tuner I2C writes tried %d, USB ctrl %d (v5: 4 tries)\n",
		       mock_tuner_wr - w, mock_ctrl_in + mock_ctrl_out - a);
		mock_no_device = 0;
		if (rtlsdr_open(&dev, 0)) { printf("reopen failed\n"); return 1; }
		rtlsdr_set_sample_rate(dev, 2400000);
		ra = rtlsdr_set_center_freq(dev, 720000000);
		want = 720000000.0 + dev->r82xx_p.int_freq;
		printf("T8 plugged in again : set_center_freq %d, LO error %.0f Hz (want 0, <300)\n",
		       ra, fabs(programmed_lo(dev->r82xx_c.xtal) - want));
	}
	rtlsdr_close(dev);
	return 0;
}
