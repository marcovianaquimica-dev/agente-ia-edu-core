"""Authentication and authorization module for AGENTE IA EDU (Fase 16).

This module provides:
- Token validation abstractions
- Authentication gateways
- Secure access control

All implementations follow the protocol pattern to allow plugging in
real OAuth/OIDC/JWT providers without changing the rest of the application.
"""

from .gateway import AuthenticationGateway, SimpleAuthenticationGateway
from .token import JWTTokenValidator, TestTokenValidator, TokenPayload, TokenValidator

__all__ = [
    "AuthenticationGateway",
    "JWTTokenValidator",
    "SimpleAuthenticationGateway",
    "TestTokenValidator",
    "TokenPayload",
    "TokenValidator",
]
