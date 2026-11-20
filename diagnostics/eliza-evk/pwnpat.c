// SPDX-License-Identifier: GPL-2.0
/*
 * pwnpat: KMS proof pattern for the Eliza EVK HDMI bring-up. Lights the
 * first connected HDMI-A connector with its preferred mode through a dumb
 * buffer (no compositor, no libdrm) and draws "pwned by Tyler" large, plus
 * the kernel release (build fingerprint) and the UTC time, refreshed every
 * second. Usage: pwnpat [seconds] [WxH]  (default 3600 s, preferred mode)
 */
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include "font8x16.h"

static uint32_t *fb; static uint32_t W, H, P;

static void fill(int x0, int y0, int w, int h, uint32_t c)
{
	for (int y = y0; y < y0 + h && y < (int)H; y++)
		for (int x = x0; x < x0 + w && x < (int)W; x++)
			if (x >= 0 && y >= 0)
				fb[y * (P / 4) + x] = c;
}

static void text(const char *s, int x, int y, int sc, uint32_t fg, uint32_t bg)
{
	for (; *s; s++, x += 8 * sc)
		for (int r = 0; r < 16; r++) {
			uint8_t bits = font8x16[(uint8_t)*s * 16 + r];
			for (int c = 0; c < 8; c++)
				fill(x + c * sc, y + r * sc, sc, sc, (bits & (0x80 >> c)) ? fg : bg);
		}
}

static int ioc(int fd, unsigned long req, void *arg)
{
	int r;

	do { r = ioctl(fd, req, arg); } while (r == -1 && (errno == EINTR || errno == EAGAIN));
	return r;
}

int main(int argc, char **argv)
{
	int secs = argc > 1 ? atoi(argv[1]) : 3600, fd = -1;
	unsigned int want_w = 0, want_h = 0;
	struct drm_mode_card_res res = {0};
	uint32_t conns[32], crtcs[16], encs[32];
	struct drm_mode_modeinfo modes[64], mode;
	struct drm_mode_get_connector gc;
	uint32_t conn_id = 0, crtc_id = 0;
	glob_t g;

	if (argc > 2)
		sscanf(argv[2], "%ux%u", &want_w, &want_h);
	glob("/dev/dri/card*", 0, NULL, &g);
	for (size_t i = 0; i < g.gl_pathc && !conn_id; i++) {
		fd = open(g.gl_pathv[i], O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(&res, 0, sizeof(res));
		res.connector_id_ptr = (uintptr_t)conns; res.count_connectors = 32;
		res.crtc_id_ptr = (uintptr_t)crtcs; res.count_crtcs = 16;
		res.encoder_id_ptr = (uintptr_t)encs; res.count_encoders = 32;
		res.fb_id_ptr = 0; res.count_fbs = 0;
		if (ioc(fd, DRM_IOCTL_MODE_GETRESOURCES, &res)) { close(fd); continue; }
		for (uint32_t c = 0; c < res.count_connectors && !conn_id; c++) {
			memset(&gc, 0, sizeof(gc));
			gc.connector_id = conns[c];
			gc.modes_ptr = (uintptr_t)modes; gc.count_modes = 64;
			if (ioc(fd, DRM_IOCTL_MODE_GETCONNECTOR, &gc))
				continue;
			printf("%s: connector %u type %u status %u modes %u\n", g.gl_pathv[i],
			       gc.connector_id, gc.connector_type, gc.connection, gc.count_modes);
			if (gc.connector_type != DRM_MODE_CONNECTOR_HDMIA || gc.connection != 1 || !gc.count_modes)
				continue;
			mode = modes[0];
			for (uint32_t m = 0; m < gc.count_modes && m < 64; m++) {
				if (want_w && modes[m].hdisplay == want_w && modes[m].vdisplay == want_h) { mode = modes[m]; break; }
				if (!want_w && (modes[m].type & DRM_MODE_TYPE_PREFERRED)) { mode = modes[m]; break; }
			}
			conn_id = gc.connector_id;
			crtc_id = crtcs[0];
		}
		if (!conn_id)
			close(fd);
	}
	if (!conn_id) { fprintf(stderr, "no connected HDMI-A connector\n"); return 1; }
	W = mode.hdisplay; H = mode.vdisplay;
	printf("mode %s %ux%u@%u clock %u kHz, crtc %u\n", mode.name, W, H, mode.vrefresh, mode.clock, crtc_id);

	struct drm_mode_create_dumb cd = { .width = W, .height = H, .bpp = 32 };
	if (ioc(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd)) { perror("create_dumb"); return 1; }
	P = cd.pitch;
	struct drm_mode_fb_cmd fbc = { .width = W, .height = H, .pitch = P, .bpp = 32, .depth = 24, .handle = cd.handle };
	if (ioc(fd, DRM_IOCTL_MODE_ADDFB, &fbc)) { perror("addfb"); return 1; }
	struct drm_mode_map_dumb md = { .handle = cd.handle };
	if (ioc(fd, DRM_IOCTL_MODE_MAP_DUMB, &md)) { perror("map_dumb"); return 1; }
	fb = mmap(0, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, md.offset);
	if (fb == MAP_FAILED) { perror("mmap"); return 1; }

	/* background: SMPTE-like bars across the bottom fifth, dark above */
	const uint32_t bars[] = { 0xffffff, 0xffff00, 0x00ffff, 0x00ff00, 0xff00ff, 0xff0000, 0x0000ff, 0x000000 };
	fill(0, 0, W, H, 0x101030);
	for (int b = 0; b < 8; b++)
		fill(b * W / 8, H * 4 / 5, W / 8 + 1, H / 5 + 1, bars[b]);

	const char *big = "pwned by Tyler";
	int sc = W / (strlen(big) * 8 + 4);
	text(big, (W - strlen(big) * 8 * sc) / 2, H / 5, sc, 0xffffff, 0x101030);

	struct drm_mode_crtc sc_ = { .crtc_id = crtc_id, .fb_id = fbc.fb_id, .set_connectors_ptr = (uintptr_t)&conn_id,
				     .count_connectors = 1, .mode = mode, .mode_valid = 1 };
	if (ioc(fd, DRM_IOCTL_MODE_SETCRTC, &sc_)) { perror("setcrtc"); return 1; }
	printf("SETCRTC ok\n");
	fflush(stdout);

	struct utsname u; uname(&u);
	char line[160];
	int small = sc / 4 > 2 ? sc / 4 : 2;
	for (int t = 0; t < secs; t++) {
		time_t now = time(NULL); struct tm tm; gmtime_r(&now, &tm);
		snprintf(line, sizeof(line), "FP %s", u.release);
		text(line, sc, H / 5 + 16 * sc + 2 * small * 16, small, 0x00ff00, 0x101030);
		strftime(line, sizeof(line), "UTC %Y-%m-%d %H:%M:%S", &tm);
		text(line, sc, H / 5 + 16 * sc + 4 * small * 16, small, 0xffff00, 0x101030);
		struct drm_mode_fb_dirty_cmd dirty = { .fb_id = fbc.fb_id };
		ioctl(fd, DRM_IOCTL_MODE_DIRTYFB, &dirty);
		if (t == 0) { printf("drawn: %s | %s\n", u.release, line); fflush(stdout); }
		sleep(1);
	}
	return 0;
}
