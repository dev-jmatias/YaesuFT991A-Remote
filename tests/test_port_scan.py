"""Finding the radio on the USB serial ports: when it fails the message says why (nothing plugged, silence, or another model answering)."""
from radio_remote.radio.cat import transport
from radio_remote.radio.cat.proto import MODELS

KNOWN = {"0670": "FT-991A", **{m.radio_id: m.name for m in MODELS.values()}}


def scan(monkeypatch, ports, answers, expect="0681", preferred=38400):
    """answers: {(port, baud): text}; anything else is silence."""
    monkeypatch.setattr(transport, "candidate_ports", lambda: ports)
    monkeypatch.setattr(transport, "probe_answer", lambda port, baud, timeout=0.6: answers.get((port, baud), ""))
    return transport.scan_ports(preferred, expect, KNOWN)


def test_found_at_the_preferred_rate(monkeypatch):
    found, why = scan(monkeypatch, ["/dev/ttyUSB0", "/dev/ttyUSB1"], {("/dev/ttyUSB0", 38400): "ID0681;"})
    assert found == ("/dev/ttyUSB0", 38400) and why == ""


def test_found_at_another_rate_including_115200(monkeypatch):
    assert scan(monkeypatch, ["/dev/ttyUSB1"], {("/dev/ttyUSB1", 4800): "ID0681;"})[0] == ("/dev/ttyUSB1", 4800)
    assert scan(monkeypatch, ["/dev/ttyUSB1"], {("/dev/ttyUSB1", 115200): "ID0681;"})[0] == ("/dev/ttyUSB1", 115200)


def test_no_port_at_all_says_so(monkeypatch):
    found, why = scan(monkeypatch, [], {})
    assert found is None and "no Silicon Labs USB serial port" in why and "lsusb" in why


def test_silence_names_every_rate_tried(monkeypatch):
    found, why = scan(monkeypatch, ["/dev/ttyUSB0", "/dev/ttyUSB1"], {})
    assert found is None and "/dev/ttyUSB0: no answer at 38400/19200/9600/4800/115200 baud" in why and "/dev/ttyUSB1" in why and "CAT rate" in why


def test_another_model_answering_is_named(monkeypatch):
    found, why = scan(monkeypatch, ["/dev/ttyUSB0", "/dev/ttyUSB1"], {("/dev/ttyUSB0", 38400): "ID0682;"})
    assert found is None
    assert "ID0682;" in why and "Yaesu FTDX101MP" in why and "choose that model" in why
    found, why = scan(monkeypatch, ["/dev/ttyUSB0"], {("/dev/ttyUSB0", 9600): "ID0670;"}, expect="0681")
    assert "FT-991A" in why and "9600" in why


def test_the_driver_error_carries_the_explanation():
    import inspect

    from radio_remote.radio.drivers import ft991a
    src = inspect.getsource(ft991a)
    assert "scan_ports" in src and "found on any USB serial port: {why}" in src