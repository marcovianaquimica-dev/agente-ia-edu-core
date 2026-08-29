"""Integration guide for external identity provider in AGENTE IA EDU.

This document explains how to integrate AGENTE IA EDU with your host platform's
authentication system.
"""

# INTEGRATION GUIDE: External Identity Provider

## Overview

AGENTE IA EDU uses an **ExternalIdentityProvider** pattern to integrate with
host platforms. This allows the same package to work with:

- **Hosted Mode**: Integrated with a platform (Google Classroom, Moodle, LMS, etc.)
- **Standalone Mode**: Running independently with its own authentication (future)

The package knows NOTHING about:
- Passwords
- JWT tokens
- OAuth secrets
- Session management
- How authentication is performed

The host platform is responsible for authentication and providing verified identity.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Host Platform (Google Classroom, Moodle, LMS, etc.)            │
│                                                                 │
│  Student makes request with credentials/session/token          │
│  ↓                                                              │
│  Host validates and extracts identity                          │
│  ↓                                                              │
│  Host creates ExternalIdentityRequest with:                    │
│    - provider: "google" / "moodle" / etc                       │
│    - external_user_id: stable user ID from host               │
│    - subject: authentication token/info (host-specific)       │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Host's ExternalIdentityProvider Implementation                  │
│                                                                 │
│  provider.resolve(request) → ExternalIdentityContext           │
│                                                                 │
│  Validates subject signature/token/claim                       │
│  Normalizes to AGENTE IA EDU identifiers                       │
│  Returns frozen ExternalIdentityContext                        │
└─────────────────────────────────────────────────────────────────┘
                            ↓
                  ┌─────────────────┐
                  │  AGENTE IA EDU  │
                  │                 │
                  │  /api/v1/..     │
                  │  (endpoints)    │
                  │                 │
                  │  Services       │
                  │  (domain)       │
                  │                 │
                  │  Database       │
                  └─────────────────┘
```

## Step 1: Implement ExternalIdentityProvider

Create a provider that implements the protocol in `identity.py`:

```python
from agente_ia_edu.identity import (
    ExternalIdentityContext,
    ExternalIdentityProvider,
    ExternalIdentityRequest,
)

class YourHostIdentityProvider:
    """Your platform's identity provider for AGENTE IA EDU."""
    
    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        """
        Args:
            request: Contains provider name and external_user_id
            
        Returns:
            Normalized identity context (frozen dataclass)
        """
        # 1. Extract authentication info from request.subject
        #    (format depends on your platform)
        
        # 2. Validate the authentication (check JWT signature, etc.)
        
        # 3. Extract user information from your system
        
        # 4. Map to AGENTE IA EDU identifiers
        
        return ExternalIdentityContext(
            provider="your_platform_name",
            external_user_id=user_id_from_your_system,
            student_id=student_id_if_applicable,
            teacher_id=teacher_id_if_applicable,
            institution_id=institution_id,
            unit_id=unit_id,
            grade_level=grade_level,
            classroom_id=classroom_id,
            roles=("student", "teacher"),  # roles from your system
        )
```

## Step 2: Override get_current_identity Dependency

The default `get_current_identity()` extracts auth from FastAPI Request.
You can override it to integrate with your framework:

```python
from fastapi import Request
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.api.dependencies import get_identity_provider

async def get_current_identity(request: Request) -> ExternalIdentityContext:
    """Your platform's integration point."""
    provider = get_identity_provider()
    
    # Extract authentication from your platform's format
    # (example: Authorization header with JWT)
    auth_header = request.headers.get("authorization", "")
    
    # Create request object for provider
    from agente_ia_edu.identity import ExternalIdentityRequest
    identity_request = ExternalIdentityRequest(
        provider="your_platform",
        external_user_id=extract_user_id(auth_header),
        subject=auth_header,  # Provider will validate
    )
    
    context = await provider.resolve(identity_request)
    return context
```

## Step 3: Register at Startup

In your FastAPI app initialization:

```python
from fastapi import FastAPI
from agente_ia_edu.api.dependencies import set_identity_provider
from agente_ia_edu.api.app import app as agente_app

# Register your provider
your_provider = YourHostIdentityProvider()
set_identity_provider(your_provider)

# Optionally override the dependency
# (if your extraction logic differs from default)
agente_app.dependency_overrides[get_current_identity] = your_get_current_identity
```

## Step 4: Test

Use the TestExternalIdentityProvider for development:

```python
from agente_ia_edu.api.dependencies import (
    TestExternalIdentityProvider,
    set_identity_provider,
)

# In development/testing
set_identity_provider(TestExternalIdentityProvider())

# Make requests with "Authorization: Bearer student:alice" header
# TestExternalIdentityProvider will return:
# {
#     external_user_id: "alice",
#     student_id: "alice",
#     roles: ("student",)
# }
```

## Important Notes

### Security

- **Never pass student ID in request body**: The identity is resolved by your provider
- **Server is authority**: All identity resolution happens server-side
- **Client cannot override**: `identity.external_user_id` is set by provider, never from client
- **Ownership checks**: All endpoints verify `attempt.external_identity_id == identity.external_user_id`

### Fields in ExternalIdentityContext

```
provider: str                        # Your platform name
external_user_id: str               # Stable user ID (required, never changes)
student_id: str | None              # Student ID (if user is a student)
teacher_id: str | None              # Teacher ID (if user is a teacher)
institution_id: str | None          # Institution/school ID
unit_id: str | None                 # Unit/district/department ID
grade_level: str | None             # Grade/level (e.g., "10", "A1")
classroom_id: str | None            # Classroom/section ID
roles: tuple[str, ...]              # ("student",) or ("teacher",) or both
metadata: dict[str, Any]            # Any additional data from provider
```

All fields except `provider` and `external_user_id` are optional.

### Endpoints

All assessment attempt endpoints automatically receive the authenticated identity:

```python
async def start_attempt(
    publication_id: UUID = Path(...),
    payload: AttemptStartRequest = Depends(),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptStartResponse:
    # identity.external_user_id is the authenticated student
    # The attempt is always created for this identity
    # No client override possible
```

## Example: Google Classroom Integration

```python
import jwt
from google.auth.transport import requests
from google.oauth2 import id_token

class GoogleClassroomIdentityProvider:
    """Integration with Google Classroom."""
    
    async def resolve(self, request):
        try:
            # Validate Google ID token from request.subject
            id_token.verify_oauth2_token(
                request.subject,
                requests.Request(),
                audience=GOOGLE_CLIENT_ID,
            )
            
            # Extract claims
            claims = jwt.decode(request.subject, options={"verify_signature": False})
            
            # Get classroom mapping
            classroom = get_classroom_for_student(claims["sub"])
            
            return ExternalIdentityContext(
                provider="google_classroom",
                external_user_id=claims["sub"],
                student_id=claims["email"].split("@")[0],
                classroom_id=classroom["id"],
                institution_id=classroom["school_id"],
                roles=("student",),
            )
        except Exception as e:
            raise ValueError(f"Invalid token: {e}")
```

## Future: Standalone Mode

When AGENTE IA EDU runs standalone (not integrated with a host), you would use:

```python
class StandaloneIdentityProvider:
    """Standalone authentication (future feature)."""
    
    async def resolve(self, request):
        # Validate JWT from your own authentication system
        # (no host platform involved)
        ...
```

The domain models (Assessment, AssessmentAttempt, etc.) do NOT change.
Only the provider changes. This is the value of the abstraction.

## No Modifications Needed

You should NOT modify:
- `src/agente_ia_edu/identity.py` (protocol is fixed)
- `src/agente_ia_edu/db/models/assessments.py` (ORM is fixed)
- Assessment domain services (business logic is fixed)

The identity integration is a clean seam between host authentication and AGENTE IA EDU business logic.
