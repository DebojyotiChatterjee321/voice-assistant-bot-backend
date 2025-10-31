import os

from dotenv import load_dotenv
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse


load_dotenv(override=True)

twilio_sid = os.environ["TWILIO_ACCOUNT_SID"]
twilio_token = os.environ["TWILIO_AUTH_TOKEN"]
from_number = os.environ["TWILIO_PHONE_NUMBER"]
to_number = os.environ["TWILIO_TEST_TO_NUMBER"]
ws_url = os.environ.get("TWILIO_STREAM_URL")

client = Client(twilio_sid, twilio_token)
response = VoiceResponse()

if ws_url:
    connect = Connect()
    connect.stream(url=ws_url)
    response.append(connect)
else:
    response.say("Hello from Twilio! This is a test call.")

call = client.calls.create(
    twiml=str(response),
    to=to_number,
    from_=from_number,
)

print("Call SID:", call.sid)
