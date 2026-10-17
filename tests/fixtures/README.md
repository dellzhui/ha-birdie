# Test fixtures

All files in this directory are **synthetic**. `pedestal_device.json` is a
device object as returned by `GET /v1/devices?include=state` of the Birdie
Cloud Bridge (docs/bridge-api.md); its values and id are made up. Nothing
here was captured from a real Birdie Pedestal device.

The integration never sees Tuya data; Tuya-format fixtures live in
`bridge/tests/fixtures`.
