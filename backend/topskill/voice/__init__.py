"""Voice mode (P2, SPEC §18.1): a real-time, interruptible spoken conversation with an agent over Gemini
Live. The browser talks to ``voice.router`` over a WebSocket; ``voice.session`` bridges it to Vertex AI
Live and settles every answered round with the text chat's billing rules."""
