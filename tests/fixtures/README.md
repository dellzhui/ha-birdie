# Test fixtures

All files in this directory are **synthetic**. They were written by hand from
the product definition in Birdie's Tuya Developer console (DP codes, types,
ranges and enum values) and from the documented shape of Tuya Cloud API
responses. Values, the device id and the product id in
`pedestal_device.json` are made up. Nothing here was captured from a real
Birdie Pedestal device or from the real Tuya Cloud.

When real responses become available (via `birdie-tuya status/spec/device`),
add them as separate `*_captured.json` files with secrets redacted, and keep
these synthetic files for edge-case tests.
