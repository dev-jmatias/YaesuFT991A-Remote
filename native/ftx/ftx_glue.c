/* Radio Remote FT8 / FT4 glue: a small C API over kgoba's ft8_lib (MIT, see LICENSE-ft8_lib) for the Python side (audio/ftx.py).
 *
 *   decode: one slot of 12 kHz float audio -> the messages in it (frequency, time offset, SNR, text)
 *   encode: a message text -> the transmit audio of one FT8 / FT4 transmission (GFSK, as the WSJT-X specification describes it)
 *
 * The decoding loop follows ft8_lib's demo/decode_ft8.c and the synthesis its demo/gen_ft8.c (both MIT, Copyright (c) 2018 Karlis Goba).
 * Build: scripts/build_ftx.sh */
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <stdint.h>
#include <stdbool.h>

#include <ft8/decode.h>
#include <ft8/encode.h>
#include <ft8/message.h>
#include <ft8/constants.h>
#include <common/monitor.h>

#define FTX_RR_ABI 1

typedef struct {
    float freq_hz;
    float dt_s;        /* seconds from the nominal start (0.5 s into the slot) */
    float snr_db;      /* WSJT-X convention: signal power in a 2500 Hz bandwidth (an estimate) */
    int score;
    char text[48];
} ftx_rr_msg;

int ftx_rr_abi(void) { return FTX_RR_ABI; }

/* ---- callsign hash table (hashed callsigns in later messages are resolved from earlier ones) ---- */
#define HT_SIZE 256
static struct { char callsign[12]; uint32_t hash; } ht[HT_SIZE];

static void ht_cleanup(uint8_t max_age)
{
    for (int i = 0; i < HT_SIZE; ++i) {
        if (ht[i].callsign[0] == '\0') continue;
        uint8_t age = (uint8_t)(ht[i].hash >> 24);
        if (age > max_age) { ht[i].callsign[0] = '\0'; ht[i].hash = 0; }
        else ht[i].hash = (((uint32_t)age + 1u) << 24) | (ht[i].hash & 0x3FFFFFu);
    }
}
static void ht_add(const char *callsign, uint32_t hash)
{
    uint16_t hash10 = (hash >> 12) & 0x3FFu;
    int idx = (hash10 * 23) % HT_SIZE;
    for (int tries = 0; tries < HT_SIZE; ++tries) {
        if (ht[idx].callsign[0] == '\0') break;
        if (((ht[idx].hash & 0x3FFFFFu) == hash) && strcmp(ht[idx].callsign, callsign) == 0) { ht[idx].hash &= 0x3FFFFFu; return; }
        idx = (idx + 1) % HT_SIZE;
    }
    if (ht[idx].callsign[0] != '\0') return;           /* full */
    strncpy(ht[idx].callsign, callsign, 11); ht[idx].callsign[11] = '\0';
    ht[idx].hash = hash;
}
static bool ht_lookup(ftx_callsign_hash_type_t type, uint32_t hash, char *callsign)
{
    uint8_t shift = (type == FTX_CALLSIGN_HASH_10_BITS) ? 12 : (type == FTX_CALLSIGN_HASH_12_BITS ? 10 : 0);
    uint16_t hash10 = (hash >> (12 - shift)) & 0x3FFu;
    int idx = (hash10 * 23) % HT_SIZE;
    for (int tries = 0; tries < HT_SIZE && ht[idx].callsign[0] != '\0'; ++tries) {
        if (((ht[idx].hash & 0x3FFFFFu) >> shift) == hash) { strcpy(callsign, ht[idx].callsign); return true; }
        idx = (idx + 1) % HT_SIZE;
    }
    callsign[0] = '\0';
    return false;
}
static ftx_callsign_hash_interface_t hash_if = { .lookup_hash = ht_lookup, .save_hash = ht_add };

void ftx_rr_hash_reset(void) { memset(ht, 0, sizeof(ht)); }

/* ---- decode ---- */
static int cmp_float(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }

/* SNR of a decoded message: the power of its tones against the median power of the bins NEAR it in the same symbols (about 180 Hz either side, its own tones left out),
 * scaled to 2500 Hz. The noise of a receiver is not flat (the audio filter and the sound card shape it, by 10 dB or more across the passband), so the noise has to be
 * measured where the signal is, not over the whole band. (ft8_lib's own "score" is only a sync strength.) Estimated from the decoder's waterfall: good to about a dB
 * on flat noise, a few dB on a busy band. */
static float estimate_snr(const monitor_t *mon, const ftx_candidate_t *cand, const ftx_message_t *msg, ftx_protocol_t proto)
{
    const ftx_waterfall_t *wf = &mon->wf;
    uint8_t tones[FT8_NN > FT4_NN ? FT8_NN : FT4_NN];
    int nn, ntone;
    if (proto == FTX_PROTOCOL_FT8) { ft8_encode(msg->payload, tones); nn = FT8_NN; ntone = 8; }
    else { ft4_encode(msg->payload, tones); nn = FT4_NN; ntone = 4; }
    const float spacing = 1.0f / mon->symbol_period;                        /* Hz per bin: 6.25 (FT8), 20.8 (FT4) */
    int half = (int)(180.0f / spacing + 0.5f); if (half < 8) half = 8;
    const int per_row = 2 * half + ntone + 4;
    float *noise = (float *)malloc(sizeof(float) * (size_t)nn * (size_t)per_row);
    if (!noise) return -30.0f;
    double sig = 0.0; int ns = 0, nnoise = 0;
    for (int k = 0; k < nn; ++k) {
        int block = cand->time_offset + k;
        if (block < 0 || block >= wf->num_blocks) continue;
        const WF_ELEM_T *row = wf->mag + (size_t)block * wf->block_stride
            + (size_t)((cand->time_sub * wf->freq_osr) + cand->freq_sub) * wf->num_bins;
        int bin0 = cand->freq_offset, tone = tones[k];
        if (bin0 + tone < 0 || bin0 + tone >= wf->num_bins) continue;
        sig += pow(10.0, WF_ELEM_MAG(row[bin0 + tone]) / 10.0); ns++;
        for (int b = bin0 - half; b <= bin0 + ntone + half; ++b) {
            if (b < 0 || b >= wf->num_bins) continue;
            if (b >= bin0 - 1 && b <= bin0 + ntone) continue;                 /* the signal's own tones and their neighbours */
            noise[nnoise++] = WF_ELEM_MAG(row[b]);
        }
    }
    float snr = -30.0f;
    if (ns > 0 && nnoise >= 8) {
        qsort(noise, nnoise, sizeof(float), cmp_float);
        float med = noise[nnoise / 2];
        float sig_db = (float)(10.0 * log10(sig / ns + 1e-30));
        /* the median of exponentially distributed noise power sits 1.6 dB under its mean; one bin (spacing Hz) -> 2500 Hz */
        snr = sig_db - (med + 1.6f) - 10.0f * log10f(2500.0f / spacing);
        if (snr < -30.0f) snr = -30.0f;
        if (snr > 40.0f) snr = 40.0f;
    }
    free(noise);
    return snr;
}

int ftx_rr_decode(const float *samples, int n, int sample_rate, int protocol, ftx_rr_msg *out, int max_out)
{
    const int kMin_score = 10, kMax_candidates = 140, kLDPC_iterations = 25, kMax_decoded = 80;
    ftx_protocol_t proto = (protocol == 4) ? FTX_PROTOCOL_FT4 : FTX_PROTOCOL_FT8;
    if (!samples || n <= 0 || max_out <= 0) return 0;
    monitor_config_t cfg = { .f_min = 200, .f_max = 3000, .sample_rate = sample_rate, .time_osr = 2, .freq_osr = 2, .protocol = proto };
    monitor_t mon;
    monitor_init(&mon, &cfg);
    for (int pos = 0; pos + mon.block_size <= n; pos += mon.block_size) monitor_process(&mon, samples + pos);

    ftx_candidate_t cands[140];
    int ncand = ftx_find_candidates(&mon.wf, kMax_candidates, cands, kMin_score);

    static ftx_message_t decoded[80];
    ftx_message_t *table[80];
    memset(table, 0, sizeof(table));
    int count = 0;
    for (int i = 0; i < ncand && count < max_out; ++i) {
        const ftx_candidate_t *c = &cands[i];
        ftx_message_t message; ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, c, kLDPC_iterations, &message, &status)) continue;
        int idx = message.hash % kMax_decoded;
        bool empty = false, dup = false;
        for (int tries = 0; tries < kMax_decoded && !empty && !dup; ++tries) {
            if (table[idx] == NULL) empty = true;
            else if (table[idx]->hash == message.hash && memcmp(table[idx]->payload, message.payload, sizeof(message.payload)) == 0) dup = true;
            else idx = (idx + 1) % kMax_decoded;
        }
        if (!empty) continue;
        decoded[idx] = message; table[idx] = &decoded[idx];
        char text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t offsets;
        if (ftx_message_decode(&message, &hash_if, text, &offsets) != FTX_MESSAGE_RC_OK) continue;
        ftx_rr_msg *m = &out[count++];
        m->freq_hz = (mon.min_bin + c->freq_offset + (float)c->freq_sub / mon.wf.freq_osr) / mon.symbol_period;
        m->dt_s = (c->time_offset + (float)c->time_sub / mon.wf.time_osr) * mon.symbol_period - 0.5f;
        m->snr_db = estimate_snr(&mon, c, &message, proto);
        m->score = c->score;
        strncpy(m->text, text, sizeof(m->text) - 1); m->text[sizeof(m->text) - 1] = '\0';
    }
    ht_cleanup(20);
    monitor_free(&mon);
    return count;
}

/* ---- encode ---- */
#define GFSK_CONST_K 5.336446f      /* pi * sqrt(2 / log(2)) */

static void gfsk_pulse(int n_spsym, float bt, float *pulse)
{
    for (int i = 0; i < 3 * n_spsym; ++i) {
        float t = i / (float)n_spsym - 1.5f;
        pulse[i] = (erff(GFSK_CONST_K * bt * (t + 0.5f)) - erff(GFSK_CONST_K * bt * (t - 0.5f))) / 2;
    }
}

static void synth_gfsk(const uint8_t *symbols, int n_sym, float f0, float bt, float symbol_period, int rate, float *signal)
{
    int n_spsym = (int)(0.5f + rate * symbol_period);
    int n_wave = n_sym * n_spsym;
    float dphi_peak = 2 * (float)M_PI / n_spsym;
    float *dphi = (float *)malloc(sizeof(float) * (n_wave + 2 * n_spsym));
    float *pulse = (float *)malloc(sizeof(float) * 3 * n_spsym);
    for (int i = 0; i < n_wave + 2 * n_spsym; ++i) dphi[i] = 2 * (float)M_PI * f0 / rate;
    gfsk_pulse(n_spsym, bt, pulse);
    for (int i = 0; i < n_sym; ++i) {
        int ib = i * n_spsym;
        for (int j = 0; j < 3 * n_spsym; ++j) dphi[j + ib] += dphi_peak * symbols[i] * pulse[j];
    }
    for (int j = 0; j < 2 * n_spsym; ++j) {          /* dummy symbols before and after, equal to the first and last */
        dphi[j] += dphi_peak * pulse[j + n_spsym] * symbols[0];
        dphi[j + n_sym * n_spsym] += dphi_peak * pulse[j] * symbols[n_sym - 1];
    }
    float phi = 0;
    for (int k = 0; k < n_wave; ++k) { signal[k] = sinf(phi); phi = fmodf(phi + dphi[k + n_spsym], 2 * (float)M_PI); }
    int n_ramp = n_spsym / 8;                        /* raised-cosine envelope on the first and last symbol */
    for (int i = 0; i < n_ramp; ++i) {
        float env = (1 - cosf(2 * (float)M_PI * i / (2 * n_ramp))) / 2;
        signal[i] *= env; signal[n_wave - 1 - i] *= env;
    }
    free(dphi); free(pulse);
}

/* Returns the number of samples written (> 0), or a negative number: -1 cannot pack the text, -2 buffer too small, -3 bad arguments.
 * canon (optional, at least 40 bytes) receives the message as it will really be sent (unpacked again). */
int ftx_rr_encode(const char *text, int protocol, float f0_hz, int sample_rate, float *out, int max_samples, char *canon)
{
    if (!text || !out || sample_rate <= 0 || f0_hz < 50.0f) return -3;
    int ft4 = (protocol == 4);
    ftx_message_t msg;
    if (ftx_message_encode(&msg, &hash_if, text) != FTX_MESSAGE_RC_OK) return -1;
    if (canon) {
        char t[FTX_MAX_MESSAGE_LENGTH]; ftx_message_offsets_t off;
        if (ftx_message_decode(&msg, &hash_if, t, &off) == FTX_MESSAGE_RC_OK) { strncpy(canon, t, 39); canon[39] = '\0'; } else canon[0] = '\0';
    }
    int nn = ft4 ? FT4_NN : FT8_NN;
    float period = ft4 ? FT4_SYMBOL_PERIOD : FT8_SYMBOL_PERIOD;
    uint8_t tones[FT4_NN];
    if (ft4) ft4_encode(msg.payload, tones); else ft8_encode(msg.payload, tones);
    int n = nn * (int)(0.5f + sample_rate * period);
    if (n > max_samples) return -2;
    synth_gfsk(tones, nn, f0_hz, ft4 ? 1.0f : 2.0f, period, sample_rate, out);
    return n;
}
