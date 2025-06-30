import os
import json
import base64
import asyncio
import websockets
import audioop
import array
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv

load_dotenv()

# --- Audio conversion helpers -------------------------------------------
RESAMPLE_RATIO = 3  # 24 kHz ➜ 8 kHz

def pcm24_to_ulaw8(b64_pcm24: str) -> str:
    """Convert base64‑encoded PCM16 24 kHz to base64 μ‑law 8 kHz."""
    pcm_bytes = base64.b64decode(b64_pcm24)
    samples = array.array("h", pcm_bytes)          # 16‑bit little‑endian
    downsampled = samples[::RESAMPLE_RATIO]        # naive down‑sampling
    mulaw = audioop.lin2ulaw(downsampled.tobytes(), 2)
    return base64.b64encode(mulaw).decode()

# -----------------------------------------------------------------------
# Inbound audio: μ‑law 8 kHz ➜ PCM‑L16 24 kHz (duplicate‑sample up‑sample)
UPSAMPLE_RATIO = 3  # 8 kHz → 24 kHz

def ulaw8_to_pcm24(b64_ulaw: str) -> str:
    """Convert base64 μ‑law 8 kHz → base64 PCM‑L16 24 kHz."""
    ulaw_bytes = base64.b64decode(b64_ulaw)
    pcm8 = audioop.ulaw2lin(ulaw_bytes, 2)      # 16‑bit PCM @ 8 kHz
    samples = array.array("h", pcm8)
    upsampled = array.array("h")
    for s in samples:
        upsampled.extend([s, s, s])             # naïve 3× up‑sample
    return base64.b64encode(upsampled.tobytes()).decode()


# --- Agent‑specific prompts & voices ------------------------------------
AGENT_CONFIG = {
    "michela": {
        "prompt": (
            "Sei l’assistente virtuale di Michela Radicchi. "
            "Il tuo compito è parlare in italiano, essere cordiale e professionale, "
            "e aiutare l’interlocutore a fissare un appuntamento telefonico con Michela "
            "per verificare la sua situazione debitoria. "
            "Offri due possibilità di richiamo: oggi alle 17:00 oppure domani alle 12:00. "
            "Chiedi quale preferisce, conferma la scelta e concludi educatamente."
        ),
        "voice": "shimmer",  # OpenAI supported voice
    },
    "andrea": {
        "prompt": (
            "Sei l'assistente di intelligenza artificiale di Francesco Caruccio "
            "Parla sempre in italiano, tono amichevole e professionale, "
            "Devi spiegare ad Andrea che questo è un prototipo di real time talking "
            "basato sull'intelligenza artificiale. "
            "Ricordagli che deve fissare l'analisi "
            "E digli che lo saluti tanto e vi sentirete presto"
        ),
        "voice": "alloy",
    },
}

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request, agent: str = "michela"):
    cfg = AGENT_CONFIG.get(agent, AGENT_CONFIG["michela"])
    response = VoiceResponse()
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f"wss://{host}/media-stream?agent={agent}")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


# Alias route for Twilio compatibility: /voice maps to the same handler as /incoming-call
@app.api_route("/voice", methods=["GET", "POST"])
async def voice_alias(request: Request):
    # Simply reuse the existing logic for incoming calls
    return await handle_incoming_call(request)

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket, agent: str = "michela"):
    """Handle WebSocket connections between Twilio and OpenAI."""
    cfg = AGENT_CONFIG.get(agent, AGENT_CONFIG["michela"])
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws, cfg)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": ulaw8_to_pcm24(data['media']['payload'])
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                        # Send initial greeting only after Twilio stream is ready
                        await send_initial_conversation_item(openai_ws)
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            



            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    event_type = response.get("type")

                    # Log di servizio
                    if event_type in LOG_EVENT_TYPES:
                        print(f"Received event: {event_type}", response)

                    # 1️⃣  Audio dell’assistente → Twilio
                    if event_type == "response.audio.delta" and "delta" in response:
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": pcm24_to_ulaw8(response["delta"])
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Start timestamp for new response: {response_start_timestamp_twilio} ms")

                        if response.get("item_id"):
                            last_assistant_item = response["item_id"]

                        await send_mark(websocket, stream_sid)

                    # 2️⃣  L’utente inizia a parlare (possibile barge‑in)
                    elif event_type == "input_audio_buffer.speech_started":
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response id {last_assistant_item}")
                            await handle_speech_started_event()

                    # 3️⃣  L’utente ha finito di parlare: commit + risposta
                    elif event_type == "input_audio_buffer.speech_stopped":
                        # commit: trascrivi il parlato
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.commit"
                        }))
                        # chiedi all’AI di rispondere
                        await openai_ws.send(json.dumps({
                            "type": "response.create"
                        }))
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Ciao"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    # Tell OpenAI to generate the assistant's response
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws, cfg):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "pcm_l16",
            "output_audio_format": "pcm_l16",   # linear‑PCM 16‑bit, 24 kHz
            "voice": cfg["voice"],
            "instructions": cfg["prompt"],
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print(f"Sending session update for agent '{cfg['voice']}':", json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))
    # Greeting will be sent after Twilio 'start' event; do not send here.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
