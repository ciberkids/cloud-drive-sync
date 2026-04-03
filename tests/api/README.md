# API Tests (Bruno)

## Prerequisites
- Bruno CLI: `npm install -g @usebruno/cli`
- Daemon running in demo mode with HTTP: `python -m cloud_drive_sync start --foreground --demo --http-port 8080`

## Run all tests
```bash
bru run --env local tests/api/
```

## Run specific folder
```bash
bru run --env local tests/api/status/
```
