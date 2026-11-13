/* Minimal fake libusb: emulates an RTL2832U + R828D (RTL-SDR Blog V4) well
 * enough to run librtlsdr's open/tune code and count USB control transfers. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include "libusb.h"

int mock_ctrl_in, mock_ctrl_out, mock_tuner_wr, mock_tuner_rd;
int mock_pll_mode;          /* 0 lock at once, 1 never lock, 2 lock after 3 status reads */
int mock_fail_writes;       /* fail this many tuner I2C writes (from now on) */
int mock_fail_reg = -1;     /* fail the next tuner write that starts at this register */
int mock_reads_since_pll;
uint8_t mock_tregs[32];     /* tuner register file as seen by the chip */
int mock_repeater, mock_i2c_no_rep; /* I2C repeater bit; tuner I2C transfers while it is off */
int mock_strict_rep;        /* scanner-v5: tuner I2C transfers with the repeater off fail (PIPE) */
int mock_reset_on_fail;     /* scanner-v6: a forced tuner write failure also resets the tuner (registers 0) */
int mock_tuner_resets;
int mock_no_device;         /* scanner-v6: dongle unplugged, every transfer LIBUSB_ERROR_NO_DEVICE */
int mock_link_io;           /* scanner-v6: link lost the Windows way, every control transfer LIBUSB_ERROR_IO */
int mock_bulk_hang;         /* scanner-v6: bulk endpoint delivers nothing */
unsigned int mock_last_bulk_timeout;
int mock_bulk_calls;
int mock_fail_reads;
int mock_fail_reg_reset;
unsigned char mock_sys[256];  /* scanner-v7: RTL2832U system block (SYSB) 0x3000..0x30ff: GPO 01, GPI 02, GPOE 03, GPD 04 */
int mock_gpi_ext;             /* scanner-v7: levels on input pins from outside */     /* scanner-v6: the mock_fail_reg failure also resets the tuner */        /* scanner-v6: fail this many tuner I2C reads (PIPE) */
static uint8_t last_reg_ptr;

static int dummy_dev, dummy_handle;

int LIBUSB_CALL libusb_init(libusb_context **ctx) { if (ctx) *ctx = NULL; return 0; }
void LIBUSB_CALL libusb_exit(libusb_context *ctx) { (void)ctx; }
ssize_t LIBUSB_CALL libusb_get_device_list(libusb_context *ctx, libusb_device ***list)
{ static libusb_device *l[2]; (void)ctx; l[0] = (libusb_device *)&dummy_dev; l[1] = NULL; *list = l; return 1; }
void LIBUSB_CALL libusb_free_device_list(libusb_device **list, int unref) { (void)list; (void)unref; }
int LIBUSB_CALL libusb_get_device_descriptor(libusb_device *dev, struct libusb_device_descriptor *d)
{ (void)dev; memset(d, 0, sizeof(*d)); d->idVendor = 0x0bda; d->idProduct = 0x2838;
  d->iManufacturer = 1; d->iProduct = 2; d->iSerialNumber = 3; return 0; }
int LIBUSB_CALL libusb_open(libusb_device *dev, libusb_device_handle **h) { (void)dev; *h = (libusb_device_handle *)&dummy_handle; return 0; }
void LIBUSB_CALL libusb_close(libusb_device_handle *h) { (void)h; }
libusb_device * LIBUSB_CALL libusb_get_device(libusb_device_handle *h) { (void)h; return (libusb_device *)&dummy_dev; }
int LIBUSB_CALL libusb_get_string_descriptor_ascii(libusb_device_handle *h, uint8_t idx, unsigned char *data, int length)
{ const char *s = idx == 1 ? "RTLSDRBlog" : idx == 2 ? "Blog V4" : "00000001"; (void)h;
  strncpy((char *)data, s, length); return (int)strlen(s); }
int LIBUSB_CALL libusb_kernel_driver_active(libusb_device_handle *h, int i) { (void)h; (void)i; return 0; }
int LIBUSB_CALL libusb_detach_kernel_driver(libusb_device_handle *h, int i) { (void)h; (void)i; return 0; }
int LIBUSB_CALL libusb_attach_kernel_driver(libusb_device_handle *h, int i) { (void)h; (void)i; return 0; }
int LIBUSB_CALL libusb_claim_interface(libusb_device_handle *h, int i) { (void)h; (void)i; return 0; }
int LIBUSB_CALL libusb_release_interface(libusb_device_handle *h, int i) { (void)h; (void)i; return 0; }
int LIBUSB_CALL libusb_reset_device(libusb_device_handle *h) { (void)h; return 0; }
int LIBUSB_CALL libusb_bulk_transfer(libusb_device_handle *h, unsigned char ep, unsigned char *d, int len, int *tr, unsigned int t)
{ /* noise around 127.4 with a little tone, ~uniform sum of 4 */
  static unsigned s = 12345; (void)h; (void)ep;
  mock_bulk_calls++; mock_last_bulk_timeout = t;
  if (mock_no_device) { if (tr) *tr = 0; return LIBUSB_ERROR_NO_DEVICE; }
  if (mock_bulk_hang) { if (tr) *tr = 0; return t ? LIBUSB_ERROR_TIMEOUT : -99; /* -99: would block for ever */ }
  for (int i = 0; i < len; i++) { int v = 0; for (int k = 0; k < 4; k++) { s = s * 1103515245u + 12345u; v += (s >> 16) & 15; } d[i] = (unsigned char)(97 + v + ((i & 63) < 32 ? 2 : -2)); }
  if (tr) *tr = len; return 0; }
struct libusb_transfer * LIBUSB_CALL libusb_alloc_transfer(int n) { (void)n; return calloc(1, sizeof(struct libusb_transfer)); }
void LIBUSB_CALL libusb_free_transfer(struct libusb_transfer *t) { free(t); }
int LIBUSB_CALL libusb_submit_transfer(struct libusb_transfer *t) { (void)t; return LIBUSB_ERROR_NOT_SUPPORTED; }
int LIBUSB_CALL libusb_cancel_transfer(struct libusb_transfer *t) { (void)t; return 0; }
int LIBUSB_CALL libusb_handle_events_timeout_completed(libusb_context *c, struct timeval *tv, int *done) { (void)c; (void)tv; (void)done; return 0; }
unsigned char * LIBUSB_CALL libusb_dev_mem_alloc(libusb_device_handle *h, size_t l) { (void)h; (void)l; return NULL; }
int LIBUSB_CALL libusb_dev_mem_free(libusb_device_handle *h, unsigned char *b, size_t l) { (void)h; (void)b; (void)l; return 0; }

static uint8_t bitrev(uint8_t b) { uint8_t r = 0; for (int i = 0; i < 8; i++) if (b & (1 << i)) r |= 1 << (7 - i); return r; }

int LIBUSB_CALL libusb_control_transfer(libusb_device_handle *h, uint8_t rt, uint8_t req,
	uint16_t wValue, uint16_t wIndex, unsigned char *data, uint16_t len, unsigned int timeout)
{
	(void)h; (void)req; (void)timeout;
	{ const char *e = getenv("MOCK_CTRL_US"); if (e) usleep(atoi(e)); }
	if (mock_no_device) { if (rt & 0x80) mock_ctrl_in++; else mock_ctrl_out++;
		if (wIndex == 0x0610 && wValue == 0x74) mock_tuner_wr++;
		if (wIndex == 0x0600 && wValue == 0x74) mock_tuner_rd++;
		return LIBUSB_ERROR_NO_DEVICE; }
	if (mock_link_io) { if (rt & 0x80) mock_ctrl_in++; else mock_ctrl_out++;
		if (wIndex == 0x0610 && wValue == 0x74) mock_tuner_wr++;
		if (wIndex == 0x0600 && wValue == 0x74) mock_tuner_rd++;
		return LIBUSB_ERROR_IO; }
	if (rt & 0x80) {               /* IN */
		mock_ctrl_in++;
		memset(data, 0, len);
		if (wIndex == 0x0600 && wValue == 0x74) {       /* R828D I2C read */
			mock_tuner_rd++;
			if (!mock_repeater) mock_i2c_no_rep++;
			if (!mock_repeater && mock_strict_rep) return LIBUSB_ERROR_PIPE;
			if (mock_fail_reads > 0) { mock_fail_reads--;
				if (mock_reset_on_fail) { memset(mock_tregs, 0, sizeof(mock_tregs)); mock_tuner_resets++; mock_reset_on_fail = 0; }
				return LIBUSB_ERROR_PIPE; }
			if (len == 1 && last_reg_ptr == 0) { data[0] = 0x69; return len; }
			/* logical (after bit reversal) status bytes */
			uint8_t logical[5] = { 0x96, 0x00, 0x5a, 0x00, 0x18 };
			int locked = 1;
			if (len == 3) {
				mock_reads_since_pll++;
				if (mock_pll_mode == 1) locked = 0;
				if (mock_pll_mode == 2 && mock_reads_since_pll <= 3) locked = 0;
			}
			if (!locked) logical[2] &= ~0x40;
			for (int i = 0; i < len && i < 5; i++) data[i] = bitrev(logical[i]);
		} else if (wIndex == 0x0200 && wValue >= 0x3000 && wValue < 0x3100) { /* SYSB read */
			if (wValue == 0x3002)   /* GPI: output pins read back GPO, inputs the outside */
				data[0] = (mock_sys[1] & mock_sys[3]) | (mock_gpi_ext & ~mock_sys[3]);
			else
				data[0] = mock_sys[wValue - 0x3000];
		} else if (wIndex == 0x0600 && wValue == 0xa0) { /* EEPROM */
			memset(data, 0xff, len);
		}
		return len;
	}
	mock_ctrl_out++;
	if (wIndex == 0x0210 && wValue >= 0x3000 && wValue < 0x3100 && len >= 1)   /* SYSB write */
		mock_sys[wValue - 0x3000] = data[0];
	if (wIndex == 0x0011 && wValue == 0x0120)                /* demod page 1 reg 0x01 */
		mock_repeater = !!(data[0] & 0x08);
	if (wIndex == 0x0610 && wValue == 0x74) {               /* R828D I2C write */
		mock_tuner_wr++;
		if (!mock_repeater) mock_i2c_no_rep++;
		if (!mock_repeater && mock_strict_rep) return LIBUSB_ERROR_PIPE;
		if (mock_fail_writes > 0) { mock_fail_writes--;
			if (mock_reset_on_fail) { memset(mock_tregs, 0, sizeof(mock_tregs)); mock_tuner_resets++; mock_reset_on_fail = 0; }
			return LIBUSB_ERROR_PIPE; }
		if (mock_fail_reg >= 0 && data[0] == mock_fail_reg) { mock_fail_reg = -1;
			if (mock_fail_reg_reset) { memset(mock_tregs, 0, sizeof(mock_tregs)); mock_tuner_resets++; mock_fail_reg_reset = 0; }
			return LIBUSB_ERROR_IO; }
		last_reg_ptr = data[0];
		for (int i = 1; i < len; i++) if (data[0] + i - 1 < 32) mock_tregs[data[0] + i - 1] = data[i];
		if (len > 1 && data[0] <= 0x14 && data[0] + len - 1 >= 0x14) mock_reads_since_pll = 0;
	}
	return len;
}
