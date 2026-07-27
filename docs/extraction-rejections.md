# Structured extraction rejections

`POST /api/extract` includes a nullable `rejection` object. It is present when
the returned extraction is not accepted as complete, usable content, including
when Argus returns partial text for diagnostic purposes.

The object is deliberately bounded and privacy-safe:

```json
{
  "code": "timeout",
  "provider": "jina",
  "quality_passed": null,
  "is_complete": null,
  "recommended_action": "retry_later",
  "attempt_count": 3,
  "last_status": "failed",
  "total_latency_ms": 1042
}
```

It never copies the requested URL, extracted text, title, token, or raw provider
error. Argus persists the same object with the extraction artifact, so the code
in the HTTP response and the durable record have identical semantics.

## Stable codes

| Code | Meaning |
|---|---|
| `quality_gate_failed` | Content was returned by an extractor but failed Argus quality gates. |
| `incomplete_content` | Content passed quality gates but completeness checks still found truncation after fallback. |
| `provider_unavailable` | No usable provider path was available. |
| `timeout` | At least one causative extractor attempt timed out. |
| `parse_error` | A provider response could not be normalized into content. |
| `unsupported_source` | The source is invalid, unsafe, or unsupported by the extraction boundary. |
| `rate_limited` | Extraction was rejected by a provider or domain rate limit. |
| `empty_result` | An extractor completed without usable content. |

`recommended_action` is one of `retry_later`, `terminal`,
`fallback_provider`, or `manual_review`. Callers should treat these values as
guidance, not authorization to bypass their own retry, privacy, or spending
policy.

Atlas and other callers may store `rejection.code` verbatim and aggregate it
without retaining source details. This contract does **not** authorize replaying
historical Atlas failures or user-owned extraction waves. It improves evidence
for new requests only; any replay remains a separately approved caller action.

Successful, complete extractions return `"rejection": null`.
