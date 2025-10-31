"""Helper script to place a Twilio outbound call for the AI voice assistant bot.

Loads Twilio credentials and call configuration from the local .env file, creates
TwiML based on the provided configuration, and triggers an outbound phone call.
"""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_twiml(stream_url: Optional[str]) -> VoiceResponse:
    response = VoiceResponse()

    if stream_url:
        connect = Connect()
        connect.stream(url=stream_url)
        response.append(connect)
        logger.info("Configured TwiML to stream audio to %s", stream_url)
    else:
        response.say("Hello from the AI voice assistant. This is a test call.")
        logger.info("Configured TwiML with default greeting")

    return response


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_runner(proxy_host: str, port: int) -> subprocess.Popen:
    script_dir = Path(__file__).resolve().parent
    cmd = [
        sys.executable,
        "bot.py",
        "-t",
        "twilio",
        "-x",
        proxy_host,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    logger.info("Starting Pipecat telephony runner on port %s", port)
    return subprocess.Popen(cmd, cwd=script_dir)


def _wait_for_runner(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None

    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            conn.request("GET", "/")
            conn.getresponse()
            conn.close()
            logger.info("Pipecat runner is accepting connections on port %s", port)
            return
        except Exception as exc:  # pragma: no cover - best effort during startup
            last_error = exc
            time.sleep(0.5)

    raise TimeoutError(f"Pipecat runner on port {port} did not start: {last_error}")


def _stop_runner(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return

    logger.info("Stopping Pipecat runner (pid=%s)", proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
        logger.warning("Runner did not exit in time; forcing kill")
        proc.kill()


def _compute_stream_url(tunnel_host: Optional[str], stream_path: str, override: Optional[str]) -> Optional[str]:
    if override:
        return override

    if not tunnel_host:
        return None

    cleaned_host = tunnel_host.replace("https://", "").replace("http://", "").rstrip("/")
    path = stream_path if stream_path.startswith("/") else f"/{stream_path}"
    return f"wss://{cleaned_host}{path}"


def main() -> int:
    load_dotenv(override=True)

    account_sid = _require_env("TWILIO_ACCOUNT_SID")
    auth_token = _require_env("TWILIO_AUTH_TOKEN")
    from_number = _require_env("TWILIO_PHONE_NUMBER")
    to_number = _require_env("TWILIO_TEST_TO_NUMBER")
    runner_port = int(os.getenv("TWILIO_LOCAL_PORT", "7860"))
    tunnel_host = os.getenv("TWILIO_TUNNEL_HOST") or os.getenv("TWILIO_PROXY_HOST")
    stream_path = os.getenv("TWILIO_STREAM_PATH", "/ws")
    stream_url = _compute_stream_url(tunnel_host, stream_path, os.getenv("TWILIO_STREAM_URL"))

    runner_proc: Optional[subprocess.Popen] = None
    started_runner = False

    if stream_url and stream_url.startswith("wss://"):
        logger.info("Using media stream URL: %s", stream_url)
    else:
        raise RuntimeError(
            "A Twilio media stream URL is required. Set TWILIO_STREAM_URL or provide TWILIO_TUNNEL_HOST."
        )

    if not _is_port_in_use(runner_port):
        if not tunnel_host:
            raise RuntimeError(
                "No tunnel host detected. Set TWILIO_TUNNEL_HOST (e.g. your ngrok hostname) before running."
            )

        runner_proc = _start_runner(tunnel_host, runner_port)
        started_runner = True
        try:
            _wait_for_runner(runner_port)
        except Exception:
            if runner_proc:
                _stop_runner(runner_proc)
            raise
    else:
        logger.info(
            "Port %s already in use; assuming Pipecat runner is active and ready to accept Twilio streams",
            runner_port,
        )

    client = Client(account_sid, auth_token)
    response = build_twiml(stream_url)

    logger.info("Placing call from %s to %s", from_number, to_number)

    call = client.calls.create(
        twiml=str(response),
        to=to_number,
        from_=from_number,
    )

    logger.success("Call initiated. SID=%s", call.sid)

    if started_runner and runner_proc:
        logger.info("Pipecat runner will remain active for this call. Press Ctrl+C to stop.")
        try:
            runner_proc.wait()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            _stop_runner(runner_proc)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI script
        logger.exception("Failed to initiate Twilio call: %s", exc)
        raise SystemExit(1)
