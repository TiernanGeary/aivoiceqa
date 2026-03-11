#!/bin/bash
# Start ngrok + aivoiceqa server, auto-update .env with new ngrok URL

set -e
cd "$(dirname "$0")"

NGROK=/home/ec2-user/.local/bin/ngrok
PYTHON=/home/ec2-user/aivoiceqa/.venv/bin/python
PORT=8050

# Kill anything on port
fuser -k ${PORT}/tcp 2>/dev/null || true
pkill -f "ngrok http" 2>/dev/null || true
sleep 1

# Start ngrok
nohup $NGROK http $PORT --log=stdout > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!
echo "ngrok started (pid=$NGROK_PID)"

# Wait for ngrok to be ready
for i in $(seq 1 10); do
  URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(t[0]['public_url'].replace('https://',''))" 2>/dev/null)
  if [ -n "$URL" ]; then break; fi
  sleep 1
done

if [ -z "$URL" ]; then
  echo "ERROR: ngrok failed to start"
  exit 1
fi

echo "ngrok tunnel: https://$URL"

# Update .env
sed -i "s|^QA_PUBLIC_URL=.*|QA_PUBLIC_URL=$URL|" .env

# Start server
nohup $PYTHON server.py > /tmp/server.log 2>&1 &
SERVER_PID=$!
echo "server started (pid=$SERVER_PID)"

sleep 3

# Verify
STATUS=$(curl -s https://$URL/api/status 2>/dev/null)
if echo "$STATUS" | grep -q "twilio_webhook"; then
  echo ""
  echo "✓ Everything is running"
  echo "  UI:      https://$URL"
  echo "  Webhook: https://$URL/incoming"
else
  echo "ERROR: server not reachable via ngrok"
  cat /tmp/server.log
fi
