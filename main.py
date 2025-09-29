name: pixeldrain-bot
services:
  - name: app
    ports:
      - port: 8000
        protocol: HTTP
    env:
      - name: BOT_TOKEN
        secret: true
      - name: PIXELDRAIN_API_KEY
        secret: true
    scalings:
      min: 1
      max: 1
