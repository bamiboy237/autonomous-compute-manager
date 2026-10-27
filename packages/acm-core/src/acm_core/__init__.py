"""Shared foundation for every ACM package.

- ``acm_core.settings`` — environment configuration (``ACM_*``)
- ``acm_core.ids`` — prefixed ULID id types
- ``acm_core.errors`` — error hierarchy with stable codes
- ``acm_core.logging`` — structlog JSON logging with trace-id correlation
- ``acm_core.domain`` — pydantic domain models, enums and state machines
"""

__version__ = "0.1.0"
