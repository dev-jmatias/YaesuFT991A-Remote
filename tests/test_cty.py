"""Country of a callsign for the FT8/FT4 tab."""
import pytest

from radio_remote import cty


@pytest.mark.parametrize("call,country", [
    ("EA1ABC", "Spain"), ("K1ABC", "United States"), ("W1AW", "United States"), ("DL1ABC", "Germany"), ("JA1ABC", "Japan"), ("VK3XYZ", "Australia"),
    ("G4ABC", "England"), ("M0DHT", "England"), ("CT1XYZ", "Portugal"), ("PA3ABC", "Netherlands"), ("F5ABC", "France"), ("I2ABC", "Italy"),
    ("W1AW/P", "United States"), ("K1ABC/QRP", "United States"), ("EA8/DL1ABC", "Canary Islands"), ("DL1ABC/EA8", "Canary Islands"), ("K1ABC/KH6", "Hawaii"),
    ("<K1ABC>", "United States"), ("3D2CCC", "Conway Reef"), ("QQ0QQQ", ""), ("<...>", ""), ("", ""),
])
def test_country_of_a_callsign(call, country):
    assert cty.country(call) == country


@pytest.mark.parametrize("text,sender", [
    ("CQ K1FM FN30", "K1FM"), ("CQ DX EA4XYZ IN80", "EA4XYZ"), ("CQ POTA N0XYZ EM28", "N0XYZ"), ("CT1XYZ K1FM -10", "K1FM"), ("K1ABC DL1ABC JO31", "DL1ABC"),
    ("CQ K1FM", "K1FM"), ("HELLO", ""), ("", ""),
])
def test_sender_of_a_message(text, sender):
    assert cty.sender_of(text) == sender
