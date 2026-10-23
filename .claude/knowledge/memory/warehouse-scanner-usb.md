---
name: warehouse-scanner-usb
description: УПД scanner = Pantum BM2300W on USB at the warehouse PC; the print agent scans via WIA (no -Scanner IP)
metadata:
  node_type: memory
  type: project
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-23T10:21:35.737Z
---

Owner 23.10.2026: the «Поставка» scan button uses the Pantum BM2300W connected by **USB** to the warehouse PC — no network/eSCL. The print agent (`tools/print-agent`, PR #654) is installed WITHOUT `-Scanner`, so it scans through WIA (Pantum driver with WIA must be installed); the result is a JPEG, recognised on the server by tesseract. The eSCL code path stays only as an unused option.

**Why:** owner corrected the setup instructions («зачем IP, мы же договорились — только USB»).
**How to apply:** never ask for the printer IP; troubleshooting starts with the Pantum driver / WIA device list on the warehouse PC and `agent.ps1 -TestScan`. Related: [[module-warehouse]], [[erp-server]].
