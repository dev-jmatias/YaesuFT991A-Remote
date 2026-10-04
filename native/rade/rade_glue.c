/*
 * rade_glue.c - a small speech-in / audio-out wrapper around the RADE V1 C library (rade_c), for Radio Remote.
 *
 * RADE itself works on FARGAN feature vectors, not on audio. Receive: modem audio (8 kHz, real) -> rade_rx -> features -> FARGAN ->
 * speech (16 kHz). Transmit: speech (16 kHz) -> LPCNet features -> rade_tx -> modem audio (8 kHz, real). This file does those two
 * pipelines on plain int16 buffers so the server can call them through ctypes. It follows rade_rx_wav.c and rade_tx_wav.c of rade_c.
 *
 * It is compiled INTO librade (see scripts/build_rade.sh), which already contains the Opus FARGAN/LPCNet code.
 * Licence: BSD-2-Clause, like rade_c.
 */
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "arch.h"
#include "cpu_support.h"
#include "fargan.h"
#include "lpcnet.h"
#include "rade_api.h"
#include "rade_dsp.h"

#define RG_ABI_VERSION 1
#define RG_EXPORT __attribute__((visibility("default")))
#define SPEECH_FRAME LPCNET_FRAME_SIZE            /* 160 samples = 10 ms at 16 kHz */

typedef struct rg {
    struct rade *r;
    LPCNetEncState *enc;
    FARGANState fargan;
    int arch;
    /* receive */
    int nin_max, n_feat_out, fargan_ready, cont_frames;
    RADE_COMP *rx_buf;
    float *feat_buf;
    float *eoo_buf;
    int16_t *q;                                    /* modem samples waiting for a whole OFDM symbol */
    int q_n, q_cap;
    float cont_buf[5 * NB_TOTAL_FEATURES];
    int sync;
    float snr;
    /* transmit */
    int n_feat_in, frames_per_mf, feat_idx, n_tx_out;
    float *features_in;
    RADE_COMP *tx_out;
    int16_t sp[SPEECH_FRAME];                      /* speech samples waiting for a whole 10 ms frame */
    int sp_n;
} rg;

static int g_init;

RG_EXPORT int rg_abi_version(void) { return RG_ABI_VERSION; }

RG_EXPORT rg *rg_open(void) {
    if (!g_init) { rade_initialize(); g_init = 1; }
    rg *g = calloc(1, sizeof(*g));
    if (!g) return NULL;
    g->r = rade_open("", RADE_VERBOSE_0);
    if (!g->r) { free(g); return NULL; }
    g->arch = opus_select_arch();
    g->enc = lpcnet_encoder_create();
    if (!g->enc) { rade_close(g->r); free(g); return NULL; }
    fargan_init(&g->fargan);
    g->nin_max = rade_nin_max(g->r);
    g->n_feat_out = rade_n_features_in_out(g->r);
    g->n_feat_in = g->n_feat_out;
    g->frames_per_mf = g->n_feat_in / RADE_NB_TOTAL_FEATURES;
    g->n_tx_out = rade_n_tx_out(g->r);
    int n_eoo = rade_n_eoo_bits(g->r);
    g->q_cap = g->nin_max * 8;
    g->rx_buf = malloc((size_t)g->nin_max * sizeof(RADE_COMP));
    g->feat_buf = malloc((size_t)g->n_feat_out * sizeof(float));
    g->eoo_buf = n_eoo ? malloc((size_t)n_eoo * sizeof(float)) : NULL;
    g->q = malloc((size_t)g->q_cap * sizeof(int16_t));
    g->features_in = calloc((size_t)g->n_feat_in, sizeof(float));
    g->tx_out = malloc((size_t)g->n_tx_out * sizeof(RADE_COMP));
    if (!g->rx_buf || !g->feat_buf || (n_eoo && !g->eoo_buf) || !g->q || !g->features_in || !g->tx_out) {
        free(g->rx_buf); free(g->feat_buf); free(g->eoo_buf); free(g->q); free(g->features_in); free(g->tx_out);
        lpcnet_encoder_destroy(g->enc); rade_close(g->r); free(g);
        return NULL;
    }
    return g;
}

RG_EXPORT void rg_close(rg *g) {
    if (!g) return;
    free(g->rx_buf); free(g->feat_buf); free(g->eoo_buf); free(g->q); free(g->features_in); free(g->tx_out);
    lpcnet_encoder_destroy(g->enc);
    rade_close(g->r);
    free(g);
}

/* Largest number of output samples one call can need for n_in input samples (so the caller can size its buffer). */
RG_EXPORT int rg_rx_max_out(rg *g, int n_in) {
    /* every OFDM symbol (nin samples, at least nin_max/2) can yield up to n_feat_out/36 frames of 160 samples */
    int symbols = (n_in + g->q_n) / (g->nin_max / 2 > 0 ? g->nin_max / 2 : 1) + 2;
    return symbols * (g->n_feat_out / RADE_NB_TOTAL_FEATURES) * SPEECH_FRAME;
}

/* Receive: n_in modem samples (8 kHz, real int16) in; speech (16 kHz int16) appended to out (at most max_out samples). Returns the number of
 * speech samples written. *sync = 1 while the receiver is locked, *snr_db = its SNR estimate (valid while locked). */
RG_EXPORT int rg_rx(rg *g, const int16_t *in, int n_in, int16_t *out, int max_out, int *sync, float *snr_db) {
    int written = 0;
    if (n_in > g->q_cap - g->q_n) n_in = g->q_cap - g->q_n;          /* a stalled caller never grows the queue without bound */
    memcpy(g->q + g->q_n, in, (size_t)n_in * sizeof(int16_t));
    g->q_n += n_in;
    for (;;) {
        int nin = rade_nin(g->r);
        if (g->q_n < nin) break;
        for (int i = 0; i < nin; i++) {                               /* real signal: imaginary part 0, scaled like rade_rx_wav */
            g->rx_buf[i].real = g->q[i] * (2.0f / RADE_INT16_SCALE);
            g->rx_buf[i].imag = 0.0f;
        }
        memmove(g->q, g->q + nin, (size_t)(g->q_n - nin) * sizeof(int16_t));
        g->q_n -= nin;
        int has_eoo = 0;
        int n_out = rade_rx(g->r, g->feat_buf, &has_eoo, g->eoo_buf, g->rx_buf);
        g->sync = rade_sync(g->r) != 0;
        if (g->sync) g->snr = rade_snrdB_3k_est(g->r);
        if (n_out <= 0) continue;
        int n_frames = n_out / RADE_NB_TOTAL_FEATURES;
        for (int fi = 0; fi < n_frames; fi++) {
            float *feat = &g->feat_buf[fi * RADE_NB_TOTAL_FEATURES];
            if (!g->fargan_ready) {                                    /* FARGAN needs 5 frames before it can start (as in rade_rx_wav) */
                memcpy(&g->cont_buf[g->cont_frames * NB_TOTAL_FEATURES], feat, (size_t)RADE_NB_TOTAL_FEATURES * sizeof(float));
                if (++g->cont_frames >= 5) {
                    float packed[5 * NB_FEATURES], zeros[FARGAN_CONT_SAMPLES];
                    for (int i = 0; i < 5; i++)
                        memcpy(&packed[i * NB_FEATURES], &g->cont_buf[i * NB_TOTAL_FEATURES], (size_t)NB_FEATURES * sizeof(float));
                    memset(zeros, 0, sizeof(zeros));
                    fargan_cont(&g->fargan, zeros, packed);
                    g->fargan_ready = 1;
                }
                continue;
            }
            float fpcm[SPEECH_FRAME];
            fargan_synthesize(&g->fargan, fpcm, feat);
            if (written + SPEECH_FRAME > max_out) continue;            /* never overrun the caller's buffer */
            for (int s = 0; s < SPEECH_FRAME; s++) {
                float v = fpcm[s] * 32768.0f;
                if (v > 32767.0f) v = 32767.0f;
                if (v < -32767.0f) v = -32767.0f;
                out[written++] = (int16_t)floor(0.5 + (double)v);
            }
        }
    }
    if (sync) *sync = g->sync;
    if (snr_db) *snr_db = g->sync ? g->snr : 0.0f;
    return written;
}

RG_EXPORT int rg_tx_max_out(rg *g, int n_in) {
    int frames = (n_in + g->sp_n) / SPEECH_FRAME + 1;
    int mfs = frames / g->frames_per_mf + 2;
    return mfs * g->n_tx_out;
}

/* Transmit: n_in speech samples (16 kHz int16) in; modem samples (8 kHz real int16) appended to out (at most max_out). A modem frame
 * is produced for every 12 speech frames (120 ms); the rest waits for the next call. Returns the number of modem samples written. */
RG_EXPORT int rg_tx(rg *g, const int16_t *in, int n_in, int16_t *out, int max_out) {
    int written = 0, pos = 0;
    while (pos < n_in) {
        int take = SPEECH_FRAME - g->sp_n;
        if (take > n_in - pos) take = n_in - pos;
        memcpy(g->sp + g->sp_n, in + pos, (size_t)take * sizeof(int16_t));
        g->sp_n += take;
        pos += take;
        if (g->sp_n < SPEECH_FRAME) break;
        g->sp_n = 0;
        lpcnet_compute_single_frame_features(g->enc, (opus_int16 *)g->sp, &g->features_in[g->feat_idx * RADE_NB_TOTAL_FEATURES], g->arch);
        if (++g->feat_idx >= g->frames_per_mf) {
            g->feat_idx = 0;
            int n = rade_tx(g->r, g->tx_out, g->features_in);
            for (int i = 0; i < n && written < max_out; i++) {
                float v = g->tx_out[i].real * RADE_INT16_SCALE;
                if (v > 32767.0f) v = 32767.0f;
                if (v < -32767.0f) v = -32767.0f;
                out[written++] = (int16_t)floor(0.5 + (double)v);
            }
        }
    }
    return written;
}
