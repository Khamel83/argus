# Research: Browser Network Attestation Authority Interface (Issue #234)

## Question

What is the exact interface contract and authorization requirement for the legitimate external browser-network attestation authority in Argus, and does an installed authority provider exist that can supply valid attestations through the current connection-policy implementation?

Specifically: qualify the retained source's attestation interface specification, identify the operator and authority responsible for provisioning valid browser-network attestations, determine if the approved provider interface and endpoint are accessible through authorized means within the current worktree, and verify whether the existing scoped implementation satisfies its exact requirements when a real attestation is available.

## Sources and Evidence

### Current Source Snapshot

- **Repository**: Khamel83/argus
- **Current commit**: `888138264ee703c11188ead8e0c9c72a231808ff`
- **Component**: `argus/acquisition/browser_policy.py` (browser connection-policy and attestation admission)
- **Test suite**: `tests/test_browser_policy.py` (hermetic admission tests without live authority)
- **Version**: 1.6.5

### BrowserNetworkAttestation Interface Specification

The required external attestation format is defined as a frozen dataclass with the following identity fields:

```python
@dataclass(frozen=True, slots=True, init=False)
class BrowserNetworkAttestation:
    policy_identity: str              # Policy issuer/version identifier
    resolver_address_control_identity: str  # Resolver/address control identity
    connection_binding_identity: str  # Source/connection binding proof
    expires_at: datetime              # Validity window (UTC)
    release_identity: str             # Bound release/version
    verification_ref: str             # Reference tag (default empty)
    verified: bool                    # Verification status (default True)
```

All fields except `verification_ref` and `verified` are required. The constructor also accepts deprecated aliases (`resolver_identity`, `address_control_identity`, `policy_id`, `connection_binding`, `connection_binding_id`, `expiry`, `release`, `candidate_release`, `candidate_release_identity`, `proof`, `proof_valid`) for backward compatibility during migration.

### BrowserAttestationProvider Interface

The external authority must implement a runtime-checkable Protocol:

```python
@runtime_checkable
class BrowserAttestationProvider(Protocol):
    """External source of a fresh browser-network attestation."""
    
    async def get_attestation(self) -> BrowserNetworkAttestation | Mapping[str, object] | None:
        """Return a fresh attestation, or ``None`` when unavailable."""
```

The provider's `get_attestation()` method must be awaitable and return either:
- A fully validated `BrowserNetworkAttestation` instance, OR
- A JSON-compatible mapping that will be coerced into `BrowserNetworkAttestation`, OR
- `None` if attestation is unavailable (triggers fail-closed admission)

### Admission Requirements

The `require_browser_policy()` function enforces these exact validations:

1. **Presence**: Attestation must be non-None (no cookies, context headers, or implicit authorization supplies attestation)
2. **Expiry**: `expires_at` must be strictly in the future at decision time (no boundary grace)
3. **Verification**: `verified` field must be `True`; any `verified=False` triggers rejection
4. **Identity binding**: All identity fields must be non-empty strings
5. **Release binding**: `release_identity` must match `current_release_identity()` (currently `argus-1.6.5`)

These validations are **source-defined** and fail-closed: the code does not accept alternate validation schemes, relaxed expiry, expired credentials repurposed as auth, or synthetic acceptance.

### Current Implementation State

**Default provider**: `UnavailableBrowserAttestationProvider` always returns `None`

```python
class UnavailableBrowserAttestationProvider:
    """Default provider. Browser capability stays disabled by default."""
    async def get_attestation(self):
        return None
```

This ensures browser acquisition fails before any browser factory or Playwright creation is attempted.

**Injection mechanism**: Global setter `set_browser_attestation_provider()` must be called with a real provider before any browser request is admitted.

**Temporary paywall exception** (issue #148): An environment-controlled opt-in allows cookie-backed paywall sites (NYTimes.com, WSJ.com) to bypass attestation for 5-minute leases. This is documented as temporary pending real browser-network authority supply.

### Test Fixture Expectations

Hermetic tests define expected attestation shape without contacting any external authority:

```python
def _attestation(**overrides) -> BrowserNetworkAttestation:
    values = {
        "policy_identity": "proxy-policy-v1",
        "resolver_identity": "resolver-v1",
        "connection_binding": "connection-binding-v1",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "release_identity": current_release_identity(),  # argus-1.6.5
    }
    values.update(overrides)
    return BrowserNetworkAttestation(**values)
```

These test values demonstrate the expected field structure, identity format, expiry pattern (future-pointing), and release binding. **No real authority receipt, network call, or session state is used in test fixtures.**

### Resource and Cleanup Guarantees

- **Browser lease duration**: Admission grants a bound lease (default: presence + identity binding validity)
- **Resource count tracking**: Each admission instance tracks blocked resource count and list for audit
- **Cleanup scope**: Resource guards abort unsafe cross-origin and non-network requests before dispatch; WebSocket guards close unsafe connections immediately
- **Finite limits**: No persistent state, no credential storage, no retry loops; exhausted authority availability returns `None` and triggers fail-closed admission

### Known Authority Gaps

1. **No installed authority**: The supplied evidence does not name an installed issuer endpoint, operator path, or accessible receipt location
2. **Operator identity**: Task description identifies "Omar" as the existing network-policy authority operator, but no endpoint or interface is supplied within the worktree
3. **Upstream dependency**: Browser capability remains blocked pending real authority supply
4. **No alternate channels**: The implementation does not accept browser launch flags, environment tokens, cookies, or implicit context as attestation substitutes

## Conclusion

### Source Interface Compliance

The current source correctly specifies the exact contract:
- ✅ Clear Protocol definition with async interface
- ✅ Immutable attestation schema with required identity fields  
- ✅ Strict validation (expiry, verification, identity binding, release matching)
- ✅ Fail-closed default (`UnavailableBrowserAttestationProvider`)
- ✅ Injection point for real providers via `set_browser_attestation_provider()`
- ✅ Test fixtures prove attestation structure without authority contact
- ✅ Resource guards and cleanup bounds are defined

The implementation **already satisfies its exact requirements** when a real attestation is available: the source will accept and validate any properly-formed attestation from an injected provider, and will correctly reject missing, expired, unverified, or mismatched attestations.

### Authority Availability

**MISSING**: No legitimate external authority endpoint or installed provider is available within the current worktree or accessible through authorized means.

- No issued attestation artifact is present
- No operator contact point is established
- No receipt, policy identity, or issuer certificate is supplied
- Task description names "Omar" as the operator but provides no endpoint
- Temporary paywall exception (issue #148) remains the only unblocked browser path

### Implications

1. **Source readiness**: The implementation is complete, testable (hermetically), and correct as specified
2. **Runtime readiness**: Browser capability cannot be enabled until an authorized external authority supplies:
   - Valid `BrowserNetworkAttestation` instances matching the source schema
   - An accessible provider endpoint (async method returning attestations)
   - Operator authorization for Argus deployment to call the authority
3. **No workaround path**: This is a genuine external security-authority limitation, not a configuration or scope issue. Synthetic approval, mock authorities, or credential substitutes do not comply with the contract.

## Next Action

1. **Source verification** (COMPLETE): The retained source correctly implements the specified interface with fail-closed defaults and strict validation. No source change is needed.

2. **Authority consultation** (BLOCKED): Verify with Omar (existing network-policy authority operator) whether:
   - An approved attestation service exists and is accessible to Argus deployments
   - An authorized provider endpoint and credentials are available
   - Argus meets baseline requirements for authority trust
   - A bounded receipt operation or test attestation is available for integration testing

3. **Integration path** (DEFERRED): Once authority consultation succeeds:
   - Document the provider interface and endpoint location
   - Implement the provider adapter matching the Protocol
   - Bind the adapter via `set_browser_attestation_provider()` in initialization
   - Add integration tests with mock/sandbox authority receipts
   - Update HANDOFF.md and operational docs with authority identity and contact

**This research does not establish that an installed authority exists or that runtime approval is available. If the authority does not exist or denies access, Argus correctly remains fail-closed.**

---

**Report generated**: 2026-11-03 (research worker session)  
**Evidence tag**: source inspection, test fixture analysis, protocol definition review  
**Limitations**: No browser launch, no live network calls, no credential access, no alternate authority consultation
