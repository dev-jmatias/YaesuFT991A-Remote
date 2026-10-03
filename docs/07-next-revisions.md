# Next revisions

## UI feedback (from the first review)

- **The tuning dial is too large.** Reduce its footprint (smaller default size, cap on tablets/desktop, maybe a compact
  strip variant), and give the freed space to meters and controls.
- **Colours are too basic; make it more colourful.** The current palette is dark with a single teal accent. Planned: a richer
  but still readable theme (distinct colours per function group: RX / TX / filters / audio / status), colour-graded meters,
  clearer TX state, optional theme choices. Needs a design pass with you before coding.

## Requested during testing

- **VFO B** (done, needs a bench check): B readout, swap (`SV`), A=B (`AB`), B=A (`BA`), set B's frequency (`FB`). There is no
  select-VFO command on the FT-991A, so "switch" means swap.
- **Split** (pending): TX on VFO B while receiving on A (`FT`, `TS`, `QS`). The manual's `FT` set values (2/3) and read values
  (0/1) disagree, as the speech-processor ones did, so the radio's real replies are needed before any code is written.
- **Trusted users** (done): admins can mark users who take control without being asked.
- **TUNE button, C4FM DG-ID** (done, TUNE needs a dummy-load bench).

## Not done yet (honest list)

| Item | Why it is open |
|---|---|
| **FTDX101D/MP, FTDX10, FT-710 drivers** | CAT manuals are available and converted, but nothing is implemented. Each needs its own capability file and driver verified against its manual (dual receivers, different opcodes), then bench testing. Selecting these models is refused today |
| Hamlib backend / rigctld compatibility | optional; native CAT was chosen as primary. Not written |
| Meter calibration (S-units, watts, SWR) | raw values only; needs bench measurements |
| CAT power-ON | `PS1;` documented but unproven in standby |
| AGC control | manual's parameter mapping is inconsistent; needs bench |
| Tuner start (`AC002`) | keys the transmitter; stop semantics unclear |
| WebSocket audio fallback | for networks where WebRTC UDP fails |
| Audio in its own process | in-process for now; decide after Pi CPU numbers |
| Installer run on a real Pi | scripts untested on Linux; logic tested in Python |
| Real-device UI testing | iPad/iPhone/Android touch, PWA install, wake lock |
| Bench results | `bench1_readonly.py`, `audio_spike.py`, `arecord -l` / `aplay -l` still to be supplied |
| Antenna switching | FT-991A has no CAT command; optional external relay interface not designed |
| TLS/Tailscale integration tests | recipes documented, not exercised |

- **Other radios** (FTDX10, FTDX101D/MP, FT-710): profiles and a shared protocol object are written from the manuals, tested only against a
  simulator, and marked experimental: see `docs/08-other-radios.md`. AGC control added for all radios.
