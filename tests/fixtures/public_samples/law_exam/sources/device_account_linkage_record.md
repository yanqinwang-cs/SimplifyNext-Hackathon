# Device and External-Service Linkage Record

## Source
University IT Forensics Unit.

## Purpose
Comparison of device DF-26-091 activity with external-service records.

## Findings

The three outbound sessions recorded on device DF-26-091 match external-service sessions by session identifier:

| Device Session | External-Service Session | Device Time | Service Time |
|---|---|---:|---:|
| `OV-4418` | `OV-4418` | 10:22:27 | 10:22:29 |
| `OV-4421` | `OV-4421` | 10:37:20 | 10:37:22 |
| `OV-4425` | `OV-4425` | 10:51:18 | 10:51:20 |

The external-service account was authenticated through the companion application on **CandidateA-Phone**.

The companion application log identifies DF-26-091 as the connected wearable for all three sessions.

The identifiers and timestamps are consistent across:
- the glasses activity database;
- the companion application;
- the external-service account export.

No additional wearable device was recorded as connected to the companion application during the assessment window.
