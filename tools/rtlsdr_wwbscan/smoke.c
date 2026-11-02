#include <windows.h>
#include <stdio.h>
typedef const char *(*ver_t)(void);
typedef unsigned (*cnt_t)(void);
typedef int (*lock_t)(void *);
typedef int (*set_t)(void *, unsigned);
int main(void) {
    HMODULE u = LoadLibraryA("libusb-1.0.dll");
    HMODULE h = LoadLibraryA("rtlsdr.dll");
    if (!h) { printf("LoadLibrary rtlsdr.dll failed: %lu\n", GetLastError()); return 1; }
    ver_t ver = (ver_t)GetProcAddress(h, "rtlsdr_wwbscan_version");
    cnt_t cnt = (cnt_t)GetProcAddress(h, "rtlsdr_get_device_count");
    lock_t lk = (lock_t)GetProcAddress(h, "rtlsdr_get_tuner_lock");
    set_t ft = (set_t)GetProcAddress(h, "rtlsdr_set_fast_tune");
    set_t rt = (set_t)GetProcAddress(h, "rtlsdr_set_read_timeout");
    printf("version: %s\n", ver ? ver() : "(missing)");
    printf("devices: %u\n", cnt());
    printf("NULL dev -> lock %d, fast %d, timeout %d\n", lk(NULL), ft(NULL, 1), rt(NULL, 2000));
    return 0;
}
