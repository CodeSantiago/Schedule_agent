"""Draft/publish workflow for tenant settings.

Implements a pragmatic first-pass draft/publish for bot/business config,
operational settings, and data/integration settings:

- Settings are saved to a ``draft`` key within their config section.
- A ``GET ?status=draft`` returns draft values; default returns published.
- ``POST /publish`` copies draft → published for a given section.

This avoids a full multi-versioning system while still giving tenants
a review-before-publish workflow for sensitive settings changes.

Example config structure:

.. code-block:: json

    {
      "bot": {
        "enabled": true,
        "greeting_text": "Published greeting",
        "_draft": {
          "greeting_text": "New draft greeting",
          "behavior_notes": "Draft behavior note"
        }
      },
      "business": {
        "display_name": "Published name",
        "_draft": {
          "display_name": "New draft name"
        }
      }
    }
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_session,
    get_tenant_principal,
    get_tenant_repo,
    require_tenant,
)
from packages.infrastructure.repositories import TenantAuditLogRepository, TenantRepository

router = APIRouter(
    prefix="/tenants/{tenant_id}/settings/draft",
    tags=["draft-publish"],
)

DRAFT_KEY = "_draft"

SECTIONS = ("bot", "business", "booking", "data")


class DraftStatusOut(BaseModel):
    """Status of draft vs published for each config section."""

    sections: dict[str, dict]
    """Keys: section names. Values: {has_draft: bool, draft_fields: list[str]}"""


class DraftPublishResult(BaseModel):
    ok: bool
    section: str
    fields_published: list[str]


def _read_settings(repo: TenantRepository) -> dict:
    settings = repo.get_settings()
    return dict(settings.config) if settings else {}


def _section_draft_status(config: dict, section: str) -> dict:
    """Return the draft status for a single section."""
    section_data = config.get(section, {})
    draft = section_data.get(DRAFT_KEY, {}) if isinstance(section_data, dict) else {}
    return {
        "has_draft": bool(draft),
        "draft_fields": list(draft.keys()) if draft else [],
    }


@router.get("", response_model=DraftStatusOut)
def get_draft_status(
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> DraftStatusOut:
    """Return draft/publish status for all config sections."""
    config = _read_settings(repo)
    sections = {}
    for section in SECTIONS:
        sections[section] = _section_draft_status(config, section)
    return DraftStatusOut(sections=sections)


@router.get("/{section}", response_model=dict)
def get_draft_section(
    section: str,
    repo: TenantRepository = Depends(get_tenant_repo),
    tenant_id: UUID = Depends(require_tenant),
) -> dict:
    """Return the draft values for a specific config section.

    Returns an empty dict if no draft exists.
    """
    if section not in SECTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown section '{section}'. Valid sections: {', '.join(SECTIONS)}",
        )
    config = _read_settings(repo)
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return {}
    return section_data.get(DRAFT_KEY, {})


@router.put("/{section}", response_model=dict)
def save_draft(
    section: str,
    payload: dict,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> dict:
    """Save draft values for a config section.

    Drafts are stored in the section's ``_draft`` key alongside the
    published values. Use ``POST /publish`` to promote drafts to active.
    """
    if section not in SECTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown section '{section}'. Valid sections: {', '.join(SECTIONS)}",
        )

    config = _read_settings(repo)
    section_data = config.setdefault(section, {})
    if not isinstance(section_data, dict):
        section_data = {}
        config[section] = section_data
    section_data[DRAFT_KEY] = dict(payload)

    repo.upsert_settings(config)
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="draft_saved",
        level="info",
        message=f"Draft saved for section '{section}'",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields={"section": section, "fields": list(payload.keys())},
    )
    session.commit()

    return dict(payload)


@router.post("/publish/{section}", response_model=DraftPublishResult)
def publish_draft(
    section: str,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> DraftPublishResult:
    """Publish draft values for a config section — copies draft → published.

    After publishing, the draft is cleared for that section.
    Requires tenant auth.
    """
    if section not in SECTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown section '{section}'. Valid sections: {', '.join(SECTIONS)}",
        )

    config = _read_settings(repo)
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        raise HTTPException(
            status_code=400,
            detail=f"Section '{section}' is not a dict; cannot apply draft",
        )

    draft = section_data.pop(DRAFT_KEY, {})
    if not draft:
        raise HTTPException(
            status_code=400,
            detail=f"No draft found for section '{section}'",
        )

    # Apply draft values to the section (merge, don't replace).
    published_fields = []
    for key, value in draft.items():
        section_data[key] = value
        published_fields.append(key)

    repo.upsert_settings(config)
    session.commit()

    audit = TenantAuditLogRepository(session, tenant_id)
    audit.log(
        event_type="draft_published",
        level="info",
        message=f"Draft published for section '{section}'",
        actor_scope="tenant",
        actor_id=str(principal.user_id) if principal else None,
        changed_fields={"section": section, "fields": published_fields},
    )
    session.commit()

    return DraftPublishResult(
        ok=True,
        section=section,
        fields_published=published_fields,
    )


@router.delete("/{section}", response_model=dict)
def discard_draft(
    section: str,
    repo: TenantRepository = Depends(get_tenant_repo),
    session: Session = Depends(get_session),
    tenant_id: UUID = Depends(require_tenant),
    principal=Depends(get_tenant_principal),
) -> dict:
    """Discard the draft for a config section without publishing."""
    if section not in SECTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown section '{section}'. Valid sections: {', '.join(SECTIONS)}",
        )

    config = _read_settings(repo)
    section_data = config.get(section, {})
    if isinstance(section_data, dict):
        discarded = section_data.pop(DRAFT_KEY, {})
        repo.upsert_settings(config)
        session.commit()

        if discarded:
            audit = TenantAuditLogRepository(session, tenant_id)
            audit.log(
                event_type="draft_discarded",
                level="info",
                message=f"Draft discarded for section '{section}'",
                actor_scope="tenant",
                actor_id=str(principal.user_id) if principal else None,
            )
            session.commit()

    return {"ok": True, "section": section}
