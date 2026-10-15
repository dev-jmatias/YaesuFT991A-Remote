/*
 * rade_text.c - a callsign in the RADE V1 end-of-over frame, for Radio Remote. See rade_text.h.
 *
 * Ported to plain C (no C++, no other dependencies) from rade_text.cpp, ldpc_encode.cpp and ldpc_decode.cpp of
 * https://github.com/tmiw/freedv-backend, which is the text channel of the FreeDV program. The framing, the 6 bit character set, the CRC, the LDPC
 * code, the interleaver and the QPSK mapping must stay bit-for-bit what that code does or other stations cannot read the callsign. Changes from it:
 * a simple encode/decode interface instead of the callback object, bounds-checked symbol loops (the original reads past the end of the array when it is
 * given the number of floats as the number of symbols), a guard against zero-amplitude symbols, and no statistics or logging.
 *
 * Original copyright and licence:
 *
 * Copyright (c) Mooneer Salem. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
 *
 * - Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
 * - Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation
 *   and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
 * GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH
 * DAMAGE.
 */
#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

#include "HRA_56_56.h"
#include "rade_text.h"

#define NSYM 56                      /* QPSK symbols that carry the text */
#define NBITS 112                    /* LDPC codeword bits = 2 x NSYM */
#define MAX_CHARS 8
#define INTERLEAVER_B 37
#define LLR_MAX 10000.0f
#define MAX_ITER 30

static float clampf(float x, float lo, float hi) { return x < lo ? lo : x > hi ? hi : x; }
/* exactly std::max: with a NaN in b it returns a (phi() overflows to NaN for very confident values and the original relies on that being ignored) */
static float maxf(float a, float b) { return (a < b) ? b : a; }

/* ---------------------------------------------------------------- character set and CRC */

/* 6 bit character set: 0 NUL, 1-9 ASCII 38-46, 10-19 '0'-'9', 20-45 'A'-'Z', 46 '/', 47 ' ' (never sent). Returns the number of characters written. */
static int callsign_to_ota(const char *in, char *out, int max_in) {
    int n = 0;
    for (int i = 0; i < max_in; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == 0) break;
        if (c >= 38 && c <= 46) out[n++] = (char)(c - 37);
        else if (c == '/') out[n++] = 46;
        else if (c >= '0' && c <= '9') out[n++] = (char)(c - '0' + 10);
        else if (c >= 'A' && c <= 'Z') out[n++] = (char)(c - 'A' + 20);
        else if (c >= 'a' && c <= 'z') out[n++] = (char)(toupper(c) - 'A' + 20);
    }
    out[n] = 0;
    return n;
}

static void ota_to_callsign(const char *in, char *out, int max_in) {
    int n = 0;
    for (int i = 0; i < max_in; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == 0) break;
        if (c >= 1 && c <= 9) out[n++] = (char)(c + 37);
        else if (c >= 10 && c <= 19) out[n++] = (char)(c - 10 + '0');
        else if (c >= 20 && c <= 45) out[n++] = (char)(c - 20 + 'A');
        else if (c == 46) out[n++] = '/';
    }
    out[n] = 0;
}

static unsigned char crc8(const char *in, int len) {
    unsigned char crc = 0x00;
    while (len-- > 0) {
        unsigned char ch = (unsigned char)*in++;
        if (ch == 0) break;
        crc ^= ch;
        for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (unsigned char)((crc << 1) ^ 0x1D) : (unsigned char)(crc << 1);
    }
    return crc;
}

/* ---------------------------------------------------------------- LDPC(112,56) encoder */

/* H = [Ha | Hb], 56 checks x 112 bits, systematic: the first 56 bits are the message and the parity half is (almost) dual-diagonal, so each parity bit
 * follows from the message bits of its row and the previous parity bit. */
static void ldpc_encode(const uint8_t s[NSYM], uint8_t cw[NBITS]) {
    memset(cw, 0, NBITS);
    memcpy(cw, s, NSYM);
    for (int i = 0; i < NSYM; i++) {
        int ctr = 0;
        for (int j = 0; j < NSYM; j++) ctr += cw[j] * HRA_56_56[i][j];
        if (i > 0) ctr += cw[NSYM + i - 1];
        cw[NSYM + i] = (ctr % 2) ? 1 : 0;
    }
}

/* ---------------------------------------------------------------- LDPC(112,56) decoder: sum-product belief propagation */

typedef struct { float real, imag; } sym_t;

static int g_init;
static int g_edges;                                  /* number of ones in H */
static int g_edge_check[NSYM * NBITS], g_edge_var[NSYM * NBITS];
static int g_check_n[NSYM], g_check_e[NSYM][NBITS];
static int g_var_n[NBITS], g_var_e[NBITS][NSYM];

static void graph_init(void) {
    if (g_init) return;
    g_edges = 0;
    memset(g_check_n, 0, sizeof(g_check_n));
    memset(g_var_n, 0, sizeof(g_var_n));
    for (int i = 0; i < NSYM; i++)
        for (int j = 0; j < NBITS; j++)
            if (HRA_56_56[i][j]) {
                int e = g_edges++;
                g_edge_check[e] = i;
                g_edge_var[e] = j;
                g_check_e[i][g_check_n[i]++] = e;
                g_var_e[j][g_var_n[j]++] = e;
            }
    g_init = 1;
}

/* built once when the library is loaded (before any thread can decode), read-only afterwards */
static void __attribute__((constructor)) graph_ctor(void) { graph_init(); }

static float phi(float x) {
    if (x < 1e-10f) return LLR_MAX;
    double expx = exp((double)x);
    if (expx < 1e-10f) return LLR_MAX;
    return (float)log((expx + 1.0f) / (expx - 1.0f));
}

static float calc_likelihood(const sym_t *sym, float var, float re, float im, float amp, float avg_amp) {
    float err_r = sym->real - re, err_i = sym->imag - im, a = amp / avg_amp;
    return -var * a * a * (err_r * err_r + err_i * err_i);
}

/* Linear constant log-map (see https://sciencedirect.com/science/article/pii/S001600321200035X). */
static float max_star(float x, float y) {
    float max_xy = maxf(x, y), absdiff = fabsf(x - y), log_xy = 0;
    if (absdiff <= 2.45) log_xy = (float)(-0.24 * absdiff + 0.596);
    else if (absdiff > 2.45 && absdiff <= 3.5) log_xy = 0.048f;
    return max_xy + log_xy;
}

/* Channel LLRs of the 56 received symbols. QPSK: 00 -> (1,0), 01 -> (0,1), 10 -> (0,-1), 11 -> (-1,0). Positive LLR = bit more likely 0. */
static void linear_log_map(const sym_t *syms, const float *amps, float noise_var, float *llr_out) {
    float mean_amp = 0;
    for (int k = 0; k < NSYM; k++) mean_amp += amps[k];
    mean_amp /= NSYM;
    float es_no = 1.0f / (2.0f * noise_var);
    for (int k = 0; k < NSYM; k++) {
        float a = amps[k], num[2], den[2];
        float lk[4] = {calc_likelihood(&syms[k], es_no, 1, 0, a, mean_amp), calc_likelihood(&syms[k], es_no, 0, 1, a, mean_amp),
                       calc_likelihood(&syms[k], es_no, 0, -1, a, mean_amp), calc_likelihood(&syms[k], es_no, -1, 0, a, mean_amp)};
        num[0] = num[1] = den[0] = den[1] = -LLR_MAX;
        den[0] = max_star(den[0], lk[0]); den[1] = max_star(den[1], lk[0]);      /* 00 */
        den[0] = max_star(den[0], lk[1]); num[1] = max_star(num[1], lk[1]);      /* 01 */
        num[0] = max_star(num[0], lk[2]); den[1] = max_star(den[1], lk[2]);      /* 10 */
        num[0] = max_star(num[0], lk[3]); num[1] = max_star(num[1], lk[3]);      /* 11 */
        llr_out[2 * k] = -clampf(num[0] - den[0], -LLR_MAX, LLR_MAX);
        llr_out[2 * k + 1] = -clampf(num[1] - den[1], -LLR_MAX, LLR_MAX);
    }
}

/* Returns 1 when all parity checks are satisfied; message[112] gets the hard decisions. */
static int ldpc_decode(const sym_t *syms, const float *amps, float noise_var, uint8_t message[NBITS]) {
    float m_vc[NSYM * NBITS], m_cv[NSYM * NBITS];                      /* on the stack: the receive chain decodes in worker threads */
    float llr_ch[NBITS];
    if (noise_var < 1e-10f) noise_var = 1e-10f;
    linear_log_map(syms, amps, noise_var, llr_ch);
    for (int e = 0; e < g_edges; e++) { m_vc[e] = llr_ch[g_edge_var[e]]; m_cv[e] = 0.0f; }
    memset(message, 0, NBITS);
    for (int iter = 0; iter < MAX_ITER; iter++) {
        for (int i = 0; i < NSYM; i++) {                              /* check-to-variable (tanh / sum-product rule) */
            int sign = 1;
            float sum = 0;
            for (int k = 0; k < g_check_n[i]; k++) {
                float v = m_vc[g_check_e[i][k]];
                sign *= (v >= 0.0f) ? 1 : -1;
                sum += maxf(0.0f, phi(fabsf(v)));
            }
            for (int k = 0; k < g_check_n[i]; k++) {
                int e = g_check_e[i][k];
                float v = m_vc[e];
                int inv_sign = (v >= 0.0f) ? 1 : -1;
                m_cv[e] = clampf((float)(inv_sign * sign) * maxf(0.0f, phi(sum - phi(fabsf(v)))), -LLR_MAX, LLR_MAX);
            }
        }
        for (int j = 0; j < NBITS; j++) {                             /* variable-to-check */
            float total = llr_ch[j];
            for (int k = 0; k < g_var_n[j]; k++) total += m_cv[g_var_e[j][k]];
            for (int k = 0; k < g_var_n[j]; k++) { int e = g_var_e[j][k]; m_vc[e] = clampf(total - m_cv[e], -LLR_MAX, LLR_MAX); }
        }
        for (int j = 0; j < NBITS; j++) {                             /* posterior, hard decision */
            float L = llr_ch[j];
            for (int k = 0; k < g_var_n[j]; k++) L += m_cv[g_var_e[j][k]];
            message[j] = (L < 0.0f) ? 1 : 0;
        }
        int ok = 1;                                                   /* H x message = 0 ? */
        for (int i = 0; i < NSYM && ok; i++) {
            int ctr = 0;
            for (int j = 0; j < NBITS; j++) ctr += HRA_56_56[i][j] * message[j];
            ok = (ctr % 2) == 0;
        }
        if (ok) return 1;
    }
    return 0;
}

/* ---------------------------------------------------------------- the text channel */

void rr_text_encode(const char *callsign, float *eoo, int n_floats) {
    if (!eoo || n_floats < NBITS) return;
    char tmp[MAX_CHARS + 2];
    memset(tmp, 0, sizeof(tmp));
    int len = callsign ? (int)strlen(callsign) : 0;
    int txt_len = callsign_to_ota(callsign ? callsign : "", &tmp[1], len < MAX_CHARS ? len : MAX_CHARS);
    if (txt_len > MAX_CHARS) txt_len = MAX_CHARS;
    tmp[0] = (char)crc8(&tmp[1], txt_len);

    uint8_t ibits[NSYM], cw[NBITS];
    memset(ibits, 0, sizeof(ibits));
    for (int i = 0; i < 8; i++) ibits[i] = (tmp[0] & (1 << i)) ? 1 : 0;                  /* the CRC first, least significant bit first */
    for (int b = 8; b < NSYM; b++) {                                                      /* then eight 6 bit characters */
        int from = b - 8;
        unsigned int byte = (unsigned char)tmp[1 + from / 6];
        if (byte & (1u << (from % 6))) ibits[b] = 1;
    }
    ldpc_encode(ibits, cw);

    uint8_t tx[NBITS];                                                                    /* interleave symbol pairs: symbol i goes to (37 i) mod 56 */
    for (int i = 0; i < NSYM; i++) {
        int n = (INTERLEAVER_B * i) % NSYM;
        tx[2 * n] = cw[2 * i];
        tx[2 * n + 1] = cw[2 * i + 1];
    }
    for (int i = 0; i < NSYM; i++) {
        uint8_t b0 = tx[2 * i], b1 = tx[2 * i + 1];
        eoo[2 * i] = (!b0 && !b1) ? 1.0f : (b0 && b1) ? -1.0f : 0.0f;
        eoo[2 * i + 1] = (!b0 && b1) ? 1.0f : (b0 && !b1) ? -1.0f : 0.0f;
    }
    for (int i = NBITS; i < n_floats; i++) eoo[i] = (i % 2) ? 0.0f : 1.0f;               /* the rest of the frame: a known pattern (I = 1, Q = 0) */
}

int rr_text_decode(const float *eoo, int n_floats, char *out) {
    if (out) out[0] = 0;
    if (!eoo || !out || n_floats < NBITS) return 0;
    int nsym = n_floats / 2;

    sym_t syms[NSYM];
    float amps[NSYM];
    for (int i = 0; i < NSYM; i++) {                                                      /* deinterleave */
        int n = (INTERLEAVER_B * i) % NSYM;
        syms[i].real = eoo[2 * n];
        syms[i].imag = eoo[2 * n + 1];
    }
    float ss = 0;                                                                         /* noise estimate from the known pattern after the text */
    int ss_cnt = 0;
    for (int i = NSYM; i < nsym; i++) {
        float re = eoo[2 * i], im = eoo[2 * i + 1], amp = sqrtf(re * re + im * im);
        if (amp > 0) {
            ss += powf(1 - re / amp, 2);
            ss += powf(0 - im / amp, 2);
            ss_cnt += 2;
        }
    }
    float noise_var = ss / (float)(ss_cnt - 1);
    if (noise_var < 1e-6f) noise_var = 1e-6f;
    for (int i = 0; i < NSYM; i++) {
        float amp = sqrtf(syms[i].real * syms[i].real + syms[i].imag * syms[i].imag);
        if (amp > 1e-12f) { syms[i].real /= amp; syms[i].imag /= amp; } else { syms[i].real = syms[i].imag = 0; amp = 1e-12f; }
        amps[i] = amp;
    }

    uint8_t msg[NBITS];
    if (!ldpc_decode(syms, amps, noise_var, msg)) return 0;
    char raw[MAX_CHARS + 2];
    memset(raw, 0, sizeof(raw));
    for (int b = 0; b < 8; b++) if (msg[b]) raw[0] |= (char)(1 << b);
    for (int b = 8; b < NSYM; b++) if (msg[b]) raw[1 + (b - 8) / 6] |= (char)(1 << ((b - 8) % 6));
    if ((unsigned char)raw[0] != crc8(&raw[1], MAX_CHARS)) return 0;
    ota_to_callsign(&raw[1], out, MAX_CHARS);
    return out[0] != 0;
}
