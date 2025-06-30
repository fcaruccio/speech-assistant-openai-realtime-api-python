# call_outbound.py
from twilio.rest import Client
import os

from dotenv import load_dotenv
load_dotenv()  # carica le variabili dal file .env, se presente

# Read credentials from environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    raise ValueError(
        "Missing one or more Twilio environment variables: "
        "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER"
    )

# Initialise Twilio REST client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Place the outbound call
call = client.calls.create(
    to="+393271781839",                    # Numero del lead
    from_=TWILIO_PHONE_NUMBER,             # Tuo numero Twilio
    url="https://4ba9-82-112-222-136.ngrok-free.app/incoming-call"  # TwiML / voice webhook
)

print("Call SID:", call.sid)
