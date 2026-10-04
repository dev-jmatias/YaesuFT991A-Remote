"""The radio input (menu 106) goes back to MIC when the last operator has left (after a short wait)."""
import asyncio

from test_phase8 import add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote.common import K_DRIVER, K_HUB


async def test_mic_input_returns_to_mic_when_the_last_operator_leaves(make_app):
    client = await make_app()
    root = await admin_user(client)
    hub, drv = client.app[K_HUB], client.app[K_DRIVER]
    hub.mic_reset_delay_s = 0.3
    assert drv.state["mic_select"] == "REAR"                          # the test radio starts on REAR
    ws, _ = await root.ws()
    await ws.close()                                                   # the only operator leaves
    await asyncio.sleep(0.1)
    assert drv.state["mic_select"] == "REAR"                          # not at once: a reload or a network blip must not flip it
    await asyncio.sleep(0.5)
    assert drv.state["mic_select"] == "MIC"
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "mic_input_reset" in events
    await close_all(root)


async def test_coming_back_in_time_cancels_the_reset(make_app):
    client = await make_app()
    root = await admin_user(client)
    hub, drv = client.app[K_HUB], client.app[K_DRIVER]
    hub.mic_reset_delay_s = 0.4
    ws, _ = await root.ws()
    await ws.close()
    await asyncio.sleep(0.15)
    ws2, _ = await root.ws()                                           # the page reconnects
    await asyncio.sleep(0.6)
    assert drv.state["mic_select"] == "REAR"
    await close_all(root)


async def test_a_listener_only_account_does_not_hold_it_on_rear_nor_cancel_the_reset(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    hub, drv = client.app[K_HUB], client.app[K_DRIVER]
    hub.mic_reset_delay_s = 0.3
    vws, _ = await v.ws()                                              # a viewer is connected (it can never transmit)
    ws, _ = await root.ws()
    await ws.close()                                                   # the last operator leaves
    await asyncio.sleep(0.7)
    assert drv.state["mic_select"] == "MIC"
    await close_all(root, v)


async def test_it_is_left_alone_while_transmitting_or_when_already_on_mic(make_app):
    client = await make_app()
    root = await admin_user(client)
    hub, drv = client.app[K_HUB], client.app[K_DRIVER]
    hub.mic_reset_delay_s = 0.2
    drv._update(tx=True)
    ws, _ = await root.ws()
    await ws.close()
    await asyncio.sleep(0.5)
    assert drv.state["mic_select"] == "REAR"                          # transmitting: the radio refuses the change, so it is not attempted
    await close_all(root)