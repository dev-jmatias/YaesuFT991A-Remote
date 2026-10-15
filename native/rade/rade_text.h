/*
 * rade_text.h - a callsign in the RADE V1 end-of-over (EOO) frame, for Radio Remote.
 *
 * The same text channel the FreeDV program uses: up to 8 characters (A-Z, 0-9, '/', and the ASCII symbols 38-46), an 8 bit CRC, LDPC(112,56) coding, bit
 * interleaving and QPSK, in the first 56 symbols of the EOO frame; the remaining EOO symbols carry a known pattern. Receivers decode it from the EOO soft bits.
 *
 * Ported to plain C from rade_text.cpp, ldpc_encode.cpp and ldpc_decode.cpp of https://github.com/tmiw/freedv-backend (BSD 2-Clause, Mooneer Salem).
 * Licence: BSD-2-Clause (see rade_text.c).
 */
#ifndef RR_RADE_TEXT_H
#define RR_RADE_TEXT_H

#ifdef __cplusplus
extern "C" {
#endif

/* Fill the n_floats EOO values for rade_tx_set_eoo_bits() (QPSK symbols as I,Q float pairs; n_floats = rade_n_eoo_bits(), at least 112) with the callsign.
 * Characters that cannot be sent (anything but letters, digits, '/' and ASCII 38-46) are skipped; letters are made upper case; at most 8 are used. */
void rr_text_encode(const char *callsign, float *eoo, int n_floats);

/* Decode a received EOO frame (the n_floats soft values rade_rx() returned with has_eoo set, same I,Q layout). Returns 1 and writes the callsign (NUL
 * terminated, at most 8 characters) to out[9] when the error correction converged and the CRC matches, else 0 and an empty string. */
int rr_text_decode(const float *eoo, int n_floats, char *out);

#ifdef __cplusplus
}
#endif

#endif
