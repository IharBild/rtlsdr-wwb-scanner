#include <stdio.h>
#include <math.h>
#include "../VARIANT/src/librtlsdr.c"

extern int mock_ctrl_in, mock_ctrl_out, mock_tuner_wr, mock_tuner_rd, mock_pll_mode, mock_fail_writes, mock_fail_reg;
extern uint8_t mock_tregs[32];
extern int mock_i2c_no_rep;
extern int mock_repeater, mock_strict_rep;

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
	rtlsdr_close(dev);
	return 0;
}
