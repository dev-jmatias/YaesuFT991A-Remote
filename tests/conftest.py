import copy
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

# Safety: tests must never be able to open real hardware.
os.environ["RADIO_REMOTE_TESTING"] = "1"

from radio_remote import config
from radio_remote.app import create_app
from radio_remote.auth import AuthStore
from radio_remote.radio.mock import MockDriver


@pytest.fixture
def cfg():
    return copy.deepcopy(config.DEFAULTS)


@pytest.fixture
async def client(cfg):
    driver = MockDriver(seed=1)
    app = create_app(cfg, driver=driver, auth=AuthStore(":memory:"))
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c
    await c.close()


async def setup_admin(client, user="admin", pw="correct horse battery"):
    r = await client.post("/api/setup", json={"username": user, "password": pw})
    assert r.status == 200, await r.text()
    return (await r.json())["csrf"]
