import json

from conftest import setup_admin
from test_web import origin

from radio_remote import app as app_module


async def test_status_and_hello_carry_the_build_id(client):
    st = await (await client.get("/api/status")).json()
    assert st["build"] == app_module.BUILD and st["build"]
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    assert hello["build"] == app_module.BUILD           # the page compares this after every reconnect
    await ws.close()


def test_build_id_is_the_release_folder_name():
    # On a Pi the code lives in /opt/radio-remote/releases/<timestamp>/backend/radio_remote/app.py
    from pathlib import Path
    assert app_module.BUILD == Path(app_module.__file__).resolve().parents[2].name


def test_frontend_reload_banner_is_wired():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "frontend" / "src"
    main_js = (src / "main.js").read_text(encoding="utf-8")
    radio_js = (src / "pages" / "radio.js").read_text(encoding="utf-8")
    assert "window.__loadedBuild ??= st.build" in main_js
    assert "m.build !== window.__loadedBuild" in radio_js and 'id="updbar"' in radio_js and "location.reload()" in radio_js
