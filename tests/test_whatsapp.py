import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from whatsapp import WhatsAppClient


@pytest.mark.asyncio
async def test_send_returns_true_on_200():
    client = WhatsAppClient("http://localhost:3000")
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"ok": True})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("whatsapp.aiohttp.ClientSession", return_value=mock_session):
        result = await client.send("+15550142", "Hello!")
    assert result is True


@pytest.mark.asyncio
async def test_send_returns_false_on_connection_error():
    client = WhatsAppClient("http://localhost:3000")
    with patch("whatsapp.aiohttp.ClientSession", side_effect=Exception("connection refused")):
        result = await client.send("+15550142", "Hello!")
    assert result is False


@pytest.mark.asyncio
async def test_send_returns_false_on_non_200():
    client = WhatsAppClient("http://localhost:3000")
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Server Error")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("whatsapp.aiohttp.ClientSession", return_value=mock_session):
        result = await client.send("+15550142", "Hello!")
    assert result is False
