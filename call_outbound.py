# call_outbound.py
from twilio.rest import Client
import os
import argparse

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

parser = argparse.ArgumentParser(description="Dial a lead with a chosen agent.")
parser.add_argument("--agent", default="michela", help="Agent name (michela, andrea, ...)")
parser.add_argument("--to", default=os.getenv("LEAD_PHONE_NUMBER", "+393271781839"), help="Lead phone number")
args = parser.parse_args()

# Initialise Twilio REST client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

WEBHOOK_URL = (
    f"https://a4ed-82-112-222-136.ngrok-free.app"
    f"/incoming-call?agent={args.agent}"
)

# Place the outbound call
call = client.calls.create(
    to=args.to,                    # Numero del lead
    from_=TWILIO_PHONE_NUMBER,     # Tuo numero Twilio
    url=WEBHOOK_URL                # TwiML / voice webhook
)

print(f"Call SID: {call.sid} (agent {args.agent})")
