echo -e "\nTest missing audio"
curl \
  -H "Content-Type: application/json" \
  -d '{"audioUrl": "http://missing-audio.com/404", "transcriptUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.txt", "metadata": "blahblah"}' \
  'http://localhost:8765/transcriptions'

echo -e "\nTest missing transcription"
curl \
  -H "Content-Type: application/json" \
  -d '{"audioUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.mp3", "transcriptUrl": "http://missing-transcript.com/404", "metadata": "blahblah"}' \
  'http://localhost:8765/transcriptions'

echo -e "\nTest synchronous request"
curl \
  -H "Content-Type: application/json" \
  -d '{"audioUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.mp3", "transcriptUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.txt", "metadata": "blahblah"}' \
  'http://localhost:8765/transcriptions?async=false'

echo -e "\nTest async request"
curl \
  -H "Content-Type: application/json" \
  -d '{"audioUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.mp3", "transcriptUrl": "https://raw.githubusercontent.com/lowerquality/gentle/master/examples/data/lucier.txt", "metadata": "blahblah"}' \
  'http://localhost:8765/transcriptions'