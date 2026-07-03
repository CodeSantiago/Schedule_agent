"""Tenant-scoped repository base.

Every concrete repository inherits from `TenantScopedRepository` and gets
two guarantees for free:

1. `tenant_id` is set at construction time and stored on the instance. It
   cannot be changed afterwards — repos are not thread-safe to re-target.
2. Every public method that takes no `tenant_id` argument filters by the
   stored one. Methods that take a `tenant_id` argument use it for the
   WHERE clause and ALSO check it matches the stored one, raising
   `TenantMismatchError` if it does not.

The discipline lives here so the application layer cannot accidentally
query across tenants. If you find yourself reaching for `session.query`
directly in the application layer, stop and add a method to the repo.
"""

from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.domain.scheduling.errors import TenantMismatchError
from packages.infrastructure.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class TenantScopedRepository(Generic[ModelT]):
    """Base class for repositories whose table has a `tenant_id` column.

    Concrete subclasses set `model` and (optionally) override `_tenant_column`
    if the column is not literally named `tenant_id`.
    """

    model: type[ModelT]
    _tenant_column: str = "tenant_id"

    def __init__(self, session: Session, tenant_id: UUID) -> None:
        if not isinstance(tenant_id, UUID):
            raise TypeError(f"tenant_id must be a UUID, got {type(tenant_id).__name__}")
        self._session = session
        self._tenant_id = tenant_id

    @property
    def session(self) -> Session:
        return self._session

    @property
    def tenant_id(self) -> UUID:
        return self._tenant_id

    def _check_tenant(self, candidate: UUID | None) -> None:
        """Ensure a candidate `tenant_id` matches the repo's bound one.

        Call this from any public method that takes an explicit `tenant_id`
        argument (e.g. delete by id, update by id) so cross-tenant writes
        fail loudly instead of silently going through.
        """
        if candidate is None:
            return  # method will use self._tenant_id
        if candidate != self._tenant_id:
            raise TenantMismatchError(
                f"tenant_id {candidate} does not match repo's bound "
                f"tenant_id {self._tenant_id}"
            )

    # --- Query helpers ----------------------------------------------------

    def _by_tenant_stmt(self):
        """Return a SELECT scoped to this tenant."""
        col = getattr(self.model, self._tenant_column)
        return select(self.model).where(col == self._tenant_id)

    def list(self) -> list[ModelT]:
        """Return all rows for this tenant."""
        return list(self._session.execute(self._by_tenant_stmt()).scalars())

    def get_by_id(self, row_id: UUID) -> ModelT | None:
        """Return the row with the given primary key, scoped to this tenant.

        Returns None if the row does not exist OR if it belongs to another
        tenant — the same outcome, so we never leak the existence of a
        cross-tenant row.
        """
        stmt = self._by_tenant_stmt().where(self.model.id == row_id)
        return self._session.execute(stmt).scalar_one_or_none()

    def _has_tenant_column(self) -> bool:
        """True when `self._tenant_column` is an actual attribute on the model.

        Repos that join through a related table (e.g. `ScheduleRepository`,
        which joins `barber_schedules` to `barbers`) override the column
        name to point at the related table's PK, but that column is not
        on `self.model` itself. Those repos MUST override `add`/`delete`
        to enforce tenant scope through the join.
        """
        return hasattr(self.model, self._tenant_column)

    def add(self, row: ModelT) -> ModelT:
        """Add a row, enforcing that its tenant_id matches this repo's.

        For repos whose `model` has a literal `tenant_id` column, the row
        is forced to the bound tenant. For repos that join through a
        related table (e.g. schedules → barbers.tenant_id), the subclass
        must override this method.
        """
        if not self._has_tenant_column():
            raise NotImplementedError(
                f"{type(self).__name__} does not own a {self._tenant_column!r} "
                f"column on {self.model.__name__}; subclasses must override `add`."
            )
        row_tenant = getattr(row, self._tenant_column)
        if row_tenant is not None and row_tenant != self._tenant_id:
            raise TenantMismatchError(
                f"row tenant_id {row_tenant} does not match repo's bound "
                f"tenant_id {self._tenant_id}"
            )
        setattr(row, self._tenant_column, self._tenant_id)
        self._session.add(row)
        return row

    def delete(self, row_id: UUID) -> bool:
        """Delete by primary key, scoped to this tenant. Returns True if a
        row was actually removed (False if it didn't exist or wasn't ours).
        """
        if not self._has_tenant_column():
            raise NotImplementedError(
                f"{type(self).__name__} must override `delete` when its "
                f"model does not carry a {self._tenant_column!r} column."
            )
        col = getattr(self.model, self._tenant_column)
        stmt = (
            delete(self.model)
            .where(col == self._tenant_id)
            .where(self.model.id == row_id)
        )
        result = self._session.execute(stmt)
        return bool(result.rowcount)
