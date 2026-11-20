import os

from dotenv import load_dotenv
from deepgram import DeepgramClient

load_dotenv()
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
AUDIO_PATH = os.path.join(DATA_DIR, "o7.mp3")
TRANSCRIPT_PATH = os.path.join(DATA_DIR, "test_transcript.txt")

client = DeepgramClient(api_key=DEEPGRAM_API_KEY)

with open(AUDIO_PATH, "rb") as audio_file:
    response = client.listen.v1.media.transcribe_file(
        request=audio_file.read(),
        model="nova-3",
        language="en",
        punctuate=True,
        smart_format=True,
        diarize=True,
        utterances=True,
    )

lines = [
    f"Speaker {utterance.speaker}: {utterance.transcript}"
    for utterance in response.results.utterances
]

with open(TRANSCRIPT_PATH, "w") as f:
    f.write("\n".join(lines))

print(f"Saved transcript to {TRANSCRIPT_PATH}")
