from pathlib import Path

import httpx
import pytest

from press_to_talk.audio import stt


@pytest.mark.parametrize("function,model", [(stt.run_stt, "doubao-transcription"),
                                          (stt.run_fallback_stt, "elevenlabs-transcription")])
def test_gateway_contract(monkeypatch, tmp_path, function, model):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"test-audio")

    def post(url, **kwargs):
        assert url == "https://gateway/v1/audio/transcriptions"
        assert kwargs["headers"] == {"Authorization": "Bearer test-token"}
        assert kwargs["files"]["file"][1].read() == b"test-audio"
        assert kwargs["data"] == {"model": model}
        assert kwargs["timeout"].connect == 5
        return httpx.Response(200, json={"text": " 南京 "}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert function("https://gateway/v1/", "test-token", audio) == "南京"


def test_missing_gateway_is_an_error():
    with pytest.raises(RuntimeError, match="PTT_STT_URL"):
        stt.run_stt("", "", Path("a.wav"))
